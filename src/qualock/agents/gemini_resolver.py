import hashlib
import json
import os
import re
from pathlib import Path

from qualock.run.process import run_process

from .base import AgentBinary, AgentSupportTree
from .support_integrity import AgentSupportIntegrityError, fingerprint_support_tree


class GeminiResolveError(RuntimeError):
    pass


_PACKAGE_NAME = "@google/gemini-cli"

_STABLE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_MIN_VALIDATED_VERSION = (0, 58, 0)
_MIN_HOST_NODE_MAJOR = 20

_REQUIRED_CLI_FLAGS = (
    "--prompt",
    "--output-format",
    "--model",
    "--approval-mode",
    "--sandbox",
    "--extensions",
    "-e",
    "--version",
)

_HELP_OPTION_RE = re.compile(r"(?<!\S)(--?[A-Za-z][A-Za-z0-9-]*)(?=[,\s]|$)")

# Vars that must never reach an npm/node subprocess probing or installing the
# pinned CLI: automation credentials, ambient Google auth state, and Gemini's
# own home/system-settings overrides, all of which could leak or redirect the
# probe onto a real account outside this resolver's control.
_SCRUBBED_ENV_VARS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_PROJECT_ID",
    "GOOGLE_CLOUD_LOCATION",
    "GEMINI_CLI_HOME",
    "GEMINI_CLI_SYSTEM_SETTINGS_PATH",
    "GEMINI_CLI_SYSTEM_DEFAULTS_PATH",
)


# Shell-interception source-contract proof. Three earlier generic
# mutable-variable dataflow heuristics were each bypassed by a differently
# shaped write sitting between obtaining and forwarding the executable, so
# this validator no longer attempts to reason about arbitrary dataflow. It
# certifies the one concrete, historically reviewed Gemini shell-interception
# source chain, and fails closed on any structural drift from it:
#
#   1. `getShellConfiguration` returns a shell configuration object whose
#      non-Windows `executable` is the bare, PATH-resolvable "bash" -- never
#      the absolute "/bin/bash" -- with the Windows configuration returned
#      from its own separate branch.
#   2. `prepareShellExecution` destructures `executable` directly out of a
#      `getShellConfiguration()` call, and the very next
#      executable-affecting statement forms
#      `resolveExecutable(executable) ?? executable`. Only whitespace and
#      comments may separate the two statements, so no mutation, rebinding,
#      shadowing, or control flow of any shape -- however written -- can come
#      between them. Its next top-level statement returns an object whose
#      top-level `resolvedExecutable` property forwards that computed binding.
#   3. `resolveExecutable` handles absolute executables in their own branch
#      and resolves everything else by searching `process.env.PATH` (or
#      `process.env["PATH"]`).
#
# Formatting, indentation, and quote style are free; structure is not.
# Generic token co-occurrence, a generically named `prepareExecution` unit,
# and any non-destructuring obtain all fail closed. No AST parser and no new
# dependency is used.
_GET_SHELL_CONFIGURATION = "getShellConfiguration"
_PREPARE_SHELL_EXECUTION = "prepareShellExecution"
_RESOLVE_EXECUTABLE = "resolveExecutable"

_UNIT_PATTERN_TEMPLATE = r"(?:function\s+|static\s+)?(?<![\w.$]){name}\s*\([^)]*\)\s*\{{"

# A configuration object's `executable:` key bound to a string literal.
_EXECUTABLE_LITERAL_RE = re.compile(r"(?<![\w$.])executable\s*:\s*(['\"])([^'\"]*)\1")
# The `executable` binding introduced by an object destructuring pattern,
# optionally renamed (`executable: exe`).
_RETURN_OBJECT_RE = re.compile(r"\breturn\s*\(?\s*\{")
_WINDOWS_CONDITION_RE = re.compile(r"isWindows[A-Za-z]*\s*\(|['\"]win32['\"]")
_ABSOLUTE_CONDITION_RE = re.compile(r"(?<![\w$])isAbsolute\s*\(")
_PATH_LOOKUP_RE = re.compile(r"process\s*\.\s*env\s*(?:\.\s*PATH(?![\w$])|\[\s*(['\"])PATH\1\s*\])")
_DESTRUCTURE_RE = re.compile(
    r"(?:const|let|var)\s*\{(?P<properties>[^{}]*)\}\s*=\s*"
    rf"(?:[\w$]+\s*\.\s*)?{_GET_SHELL_CONFIGURATION}\s*\(\s*\)\s*;"
)

# Published Gemini CLI 0.58.0 compatibility path.  This deliberately does not
# feed `prepareExecution` into the historical generic unit matcher: only the
# exact static/async five-parameter method shape is eligible.
_PUBLISHED_058_PREPARE_METHOD_RE = re.compile(
    r"(?<![\w$])static\s+async\s+prepareExecution\s*\(\s*"
    r"commandToExecute\s*,\s*cwd\s*,\s*shellExecutionConfig\s*,\s*"
    r"isInteractive\s*,\s*usingPty\s*\)\s*\{"
)
_PUBLISHED_058_SANDBOX_MANAGER_RE = re.compile(
    r"const\s+sandboxManager\s*=\s*shellExecutionConfig\s*\.\s*"
    r"sandboxManager\s*\?\?\s*new\s+NoopSandboxManager\s*\(\s*\)\s*;"
)
_PUBLISHED_058_WINDOWS_RE = re.compile(
    r"const\s+(?P<windows>[\w$]+)\s*=\s*[\w$]+\s*\.\s*platform\s*\(\s*\)"
    r"\s*===\s*(['\"])win32\2\s*;"
)
_PUBLISHED_058_STRICT_RE = re.compile(r"const\s+(?P<strict>[\w$]+)\s*=\s*(?P<expression>[^;]+);")
_PUBLISHED_058_GUARD_RE_TEMPLATE = r"if\s*\(\s*{strict}\s*\)\s*\{{"
_PUBLISHED_058_CMD_BRANCH_RE = re.compile(
    r"\{\s*shell\s*=\s*(['\"])cmd\1\s*;\s*"
    r"argsPrefix\s*=\s*\[\s*(['\"])/c\2\s*\]\s*;\s*"
    r"executable\s*=\s*(['\"])cmd\.exe\3\s*;\s*\}"
)
_CMD_EXECUTABLE_WRITE_RE = re.compile(r"(?<![\w$])executable\s*=\s*(['\"])cmd\.exe\1\s*;")
_PUBLISHED_058_PREPARE_COMMAND_ASSIGNMENT_RE = re.compile(
    r"(?:const|let)\s+[\w$]+\s*=\s*await\s+sandboxManager\s*\.\s*"
    r"prepareCommand\s*\("
)


def _match_brace_scope(text: str, open_index: int) -> str | None:
    depth = 0
    in_string: str | None = None
    index = open_index
    while index < len(text):
        char = text[index]
        if in_string is not None:
            if char == "\\":
                index += 2
                continue
            if char == in_string:
                in_string = None
        elif char in ("'", '"', "`"):
            in_string = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_index : index + 1]
        index += 1
    return None


def _match_paren_scope(text: str, open_index: int) -> str | None:
    depth = 0
    in_string: str | None = None
    index = open_index
    while index < len(text):
        char = text[index]
        if in_string is not None:
            if char == "\\":
                index += 2
                continue
            if char == in_string:
                in_string = None
        elif char in ("'", '"', "`"):
            in_string = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[open_index : index + 1]
        index += 1
    return None


def _find_unit_bodies(text: str, name: str) -> list[str]:
    pattern = re.compile(_UNIT_PATTERN_TEMPLATE.format(name=re.escape(name)))
    bodies: list[str] = []
    for match in pattern.finditer(text):
        brace_index = match.end() - 1
        body = _match_brace_scope(text, brace_index)
        if body is not None:
            bodies.append(body)
    return bodies


def _lexical_matches(text: str, pattern: re.Pattern[str]) -> list[re.Match[str]]:
    """Find pattern matches in code, never inside strings or comments."""
    matches: list[re.Match[str]] = []
    in_string: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if in_string is not None:
            if char == "\\":
                index += 2
                continue
            if char == in_string:
                in_string = None
            index += 1
            continue
        if text.startswith("//", index):
            end = index + 2
            while end < len(text) and text[end] not in "\r\n\u2028\u2029":
                end += 1
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end == -1:
                return []
            index = end + 2
            continue
        if char in ("'", '"', "`"):
            in_string = char
            index += 1
            continue
        match = pattern.match(text, index)
        if match is not None:
            matches.append(match)
        index += 1
    return matches


def _find_published_058_prepare_bodies(text: str) -> list[str]:
    bodies: list[str] = []
    for match in _lexical_matches(text, _PUBLISHED_058_PREPARE_METHOD_RE):
        brace_index = match.end() - 1
        body = _match_brace_scope(text, brace_index)
        if body is not None:
            bodies.append(body)
    return bodies


def _top_level_matches(text: str, pattern: re.Pattern[str]) -> list[re.Match[str]]:
    """Find pattern matches outside nested delimiters, strings, and comments."""
    matches: list[re.Match[str]] = []
    braces = 0
    brackets = 0
    parens = 0
    in_string: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if in_string is not None:
            if char == "\\":
                index += 2
                continue
            if char == in_string:
                in_string = None
            index += 1
            continue
        if text.startswith("//", index):
            end = index + 2
            while end < len(text) and text[end] not in "\r\n\u2028\u2029":
                end += 1
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end == -1:
                return []
            index = end + 2
            continue
        if char in ("'", '"', "`"):
            in_string = char
            index += 1
            continue
        if braces == 0 and brackets == 0 and parens == 0:
            match = pattern.match(text, index)
            if match is not None:
                matches.append(match)
        if char == "{":
            braces += 1
        elif char == "}":
            braces -= 1
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets -= 1
        elif char == "(":
            parens += 1
        elif char == ")":
            parens -= 1
        if braces < 0 or brackets < 0 or parens < 0:
            return []
        index += 1
    if in_string is not None or braces or brackets or parens:
        return []
    return matches


def _top_level_member_call_openings(text: str, receiver: str, member: str) -> list[int] | None:
    """Locate direct member calls, ignoring trivia and grouping parentheses."""
    openings: list[int] = []
    previous_tokens: list[str] = []
    braces = 0
    brackets = 0
    parens = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if text.startswith("//", index):
            end = index + 2
            while end < len(text) and text[end] not in "\r\n\u2028\u2029":
                end += 1
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end == -1:
                return None
            index = end + 2
            continue
        if char in ("'", '"', "`"):
            quote = char
            index += 1
            while index < len(text) and text[index] != quote:
                if text[index] == "\\":
                    index += 2
                else:
                    index += 1
            if index == len(text):
                return None
            index += 1
            token = "<literal>"
        elif char.isalpha() or char in "_$":
            end = index + 1
            while end < len(text) and (text[end].isalnum() or text[end] in "_$"):
                end += 1
            token = text[index:end]
            index = end
        else:
            token = char
            if (
                token == "("
                and braces == 0
                and brackets == 0
                and previous_tokens[-3:] == [receiver, ".", member]
            ):
                openings.append(index)
            index += 1

        if token == "{":
            braces += 1
        elif token == "}":
            braces -= 1
        elif token == "[":
            brackets += 1
        elif token == "]":
            brackets -= 1
        elif token == "(":
            parens += 1
        elif token == ")":
            parens -= 1
        if braces < 0 or brackets < 0 or parens < 0:
            return None
        previous_tokens.append(token)
        previous_tokens = previous_tokens[-3:]

    if braces or brackets or parens:
        return None
    return openings


def _conditional_branch(body: str, condition_pattern: re.Pattern[str]) -> str | None:
    # The braced consequent of the first `if` whose condition matches
    # `condition_pattern`. A braceless consequent is not recognised, so a
    # bundle that writes one fails closed rather than being certified from a
    # branch this scanner cannot bound.
    for match in re.finditer(r"\bif\s*\(", body):
        open_paren = match.end() - 1
        condition = _match_paren_scope(body, open_paren)
        if condition is None or not condition_pattern.search(condition):
            continue
        tail = body[open_paren + len(condition) :]
        stripped = tail.lstrip()
        if not stripped.startswith("{"):
            continue
        return _match_brace_scope(body, len(body) - len(stripped))
    return None


def _blank_out(body: str, fragment: str) -> str:
    # Replaces one bounded fragment with spaces (newlines preserved) so the
    # remaining text can be searched as "everything outside that branch".
    index = body.find(fragment)
    if index == -1:
        return body
    blanked = "".join("\n" if char == "\n" else " " for char in fragment)
    return body[:index] + blanked + body[index + len(fragment) :]


def _strip_leading_trivia(text: str) -> str:
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end == -1 else end + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index)
            if end == -1:
                return ""
            index = end + 2
            continue
        break
    return text[index:]


def _configuration_executables(scope: str) -> list[str]:
    values: list[str] = []
    for match in _RETURN_OBJECT_RE.finditer(scope):
        returned_object = _match_brace_scope(scope, match.end() - 1)
        if returned_object is None:
            continue
        values.extend(
            literal.group(2) for literal in _EXECUTABLE_LITERAL_RE.finditer(returned_object)
        )
    return values


def _returns_bare_bash_outside_windows(get_shell_configuration_body: str) -> bool:
    windows_branch = _conditional_branch(get_shell_configuration_body, _WINDOWS_CONDITION_RE)
    if windows_branch is None or not _EXECUTABLE_LITERAL_RE.search(windows_branch):
        return False
    non_windows = _blank_out(get_shell_configuration_body, windows_branch)
    executables = _configuration_executables(non_windows)
    # Every non-Windows configuration object must select the bare 'bash';
    # a single absolute (or otherwise different) selection fails this link
    # closed rather than being outvoted by a sibling bare literal.
    return bool(executables) and all(value == "bash" for value in executables)


def _resolve_statement_pattern(name: str) -> re.Pattern[str]:
    identifier = re.escape(name)
    return re.compile(
        r"(?:const|let|var)\s+(?P<resolved>[\w$]+)\s*=\s*"
        rf"(?:[\w$]+\s*\.\s*)?{_RESOLVE_EXECUTABLE}\s*\(\s*{identifier}\s*\)"
        rf"\s*\?\?\s*{identifier}(?![\w$])"
    )


def _trivia_end(text: str, index: int = 0) -> tuple[int, bool] | None:
    saw_line_terminator = False
    while index < len(text):
        char = text[index]
        if char.isspace():
            saw_line_terminator |= char in "\r\n\u2028\u2029"
            index += 1
            continue
        if text.startswith("//", index):
            end = index + 2
            while end < len(text) and text[end] not in "\r\n\u2028\u2029":
                end += 1
            if end == len(text):
                return end, saw_line_terminator
            saw_line_terminator = True
            index = end + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end == -1:
                return None
            saw_line_terminator |= any(
                marker in text[index : end + 2] for marker in "\r\n\u2028\u2029"
            )
            index = end + 2
            continue
        break
    return index, saw_line_terminator


def _top_level_object_properties(text: str, open_index: int) -> list[str] | None:
    properties: list[str] = []
    start = open_index + 1
    index = start
    braces = 1
    brackets = 0
    parens = 0
    in_string: str | None = None
    while index < len(text):
        char = text[index]
        if in_string is not None:
            if char == "\\":
                index += 2
                continue
            if char == in_string:
                in_string = None
            index += 1
            continue
        if text.startswith("//", index):
            end = index + 2
            while end < len(text) and text[end] not in "\r\n\u2028\u2029":
                end += 1
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end == -1:
                return None
            index = end + 2
            continue
        if char in ("'", '"', "`"):
            in_string = char
        elif char == "{":
            braces += 1
        elif char == "}":
            braces -= 1
            if braces == 0:
                if brackets or parens:
                    return None
                properties.append(text[start:index])
                return properties
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets -= 1
            if brackets < 0:
                return None
        elif char == "(":
            parens += 1
        elif char == ")":
            parens -= 1
            if parens < 0:
                return None
        elif char == "," and braces == 1 and brackets == 0 and parens == 0:
            properties.append(text[start:index])
            start = index + 1
        index += 1
    return None


def _property_tokens(property_text: str) -> list[str] | None:
    tokens: list[str] = []
    index = 0
    while index < len(property_text):
        trivia = _trivia_end(property_text, index)
        if trivia is None:
            return None
        index = trivia[0]
        if index == len(property_text):
            break
        if property_text.startswith("...", index):
            tokens.append("...")
            index += 3
            continue
        char = property_text[index]
        if char.isalpha() or char in "_$":
            end = index + 1
            while end < len(property_text) and (
                property_text[end].isalnum() or property_text[end] in "_$"
            ):
                end += 1
            tokens.append(property_text[index:end])
            index = end
            continue
        if char in ("'", '"', "`"):
            quote = char
            end = index + 1
            while end < len(property_text):
                if property_text[end] == "\\":
                    end += 2
                    continue
                if property_text[end] == quote:
                    break
                end += 1
            if end == len(property_text):
                return None
            tokens.append(property_text[index : end + 1])
            index = end + 1
            continue
        tokens.append(char)
        index += 1
    return tokens


def _destructured_executable_binding(properties_text: str) -> str | None:
    properties = _top_level_object_properties("{" + properties_text + "}", 0)
    if properties is None:
        return None
    bindings: list[str] = []
    for property_text in properties:
        tokens = _property_tokens(property_text)
        if tokens is None:
            return None
        if tokens == ["executable"]:
            bindings.append("executable")
        elif (
            len(tokens) == 3
            and tokens[:2] == ["executable", ":"]
            and re.fullmatch(r"[\w$]+", tokens[2])
        ):
            bindings.append(tokens[2])
        elif tokens and tokens[0] == "executable":
            return None
    return bindings[0] if len(bindings) == 1 else None


def _return_object_forwards_binding(statement: str, binding: str) -> bool:
    if not statement.startswith("return") or (
        len(statement) > len("return")
        and (statement[len("return")].isalnum() or statement[len("return")] in "_$")
    ):
        return False
    trivia = _trivia_end(statement, len("return"))
    if trivia is None or trivia[1]:
        return False
    open_index = trivia[0]
    if open_index == len(statement) or statement[open_index] != "{":
        return False
    properties = _top_level_object_properties(statement, open_index)
    if properties is None:
        return False

    resolved_properties = 0
    for property_text in properties:
        tokens = _property_tokens(property_text)
        if tokens is None:
            return False
        if not tokens:
            continue
        if tokens[0] == "..." or tokens[0] == "[" or tokens[0][0] in "'\"`":
            return False
        if len(tokens) > 1 and tokens[1] != ":":
            return False
        if tokens[0] != "resolvedExecutable":
            continue
        resolved_properties += 1
        if tokens == ["resolvedExecutable"]:
            if binding != "resolvedExecutable":
                return False
        elif tokens != ["resolvedExecutable", ":", binding]:
            return False
    return resolved_properties == 1


def _object_forwards_unique_binding(object_text: str, property_name: str, binding: str) -> bool:
    if not object_text.startswith("{"):
        return False
    properties = _top_level_object_properties(object_text, 0)
    if properties is None:
        return False

    matching_properties = 0
    for property_text in properties:
        tokens = _property_tokens(property_text)
        if tokens is None:
            return False
        if not tokens:
            continue
        # A top-level spread, computed key, or quoted key could obscure or
        # override `command`, so this exact compatibility proof rejects the
        # whole call object when any one is present.
        if tokens[0] == "..." or tokens[0] == "[" or tokens[0][0] in "'\"`":
            return False
        # Apart from ordinary `key: value` entries, only identifier shorthand
        # is accepted. Accessors and methods can define a later `command` key
        # while placing `get`, `set`, `async`, or `*` in the first token.
        if len(tokens) > 1 and tokens[1] != ":":
            return False
        if tokens[0] != property_name:
            continue
        matching_properties += 1
        if tokens != [property_name, ":", binding]:
            return False
    return matching_properties == 1


def _destructures_then_resolves_executable(prepare_shell_execution_body: str) -> bool:
    if not prepare_shell_execution_body.startswith("{"):
        return False
    prefix = _strip_leading_trivia(prepare_shell_execution_body[1:])
    destructure = _DESTRUCTURE_RE.match(prefix)
    if destructure is None:
        return False
    name = _destructured_executable_binding(destructure.group("properties"))
    if name is None:
        return False

    following = _strip_leading_trivia(prefix[destructure.end() :])
    resolve = _resolve_statement_pattern(name).match(following)
    if resolve is None:
        return False
    terminator = _trivia_end(following, resolve.end())
    if terminator is None:
        return False
    after_expression, saw_line_terminator = terminator
    if after_expression < len(following) and following[after_expression] == ";":
        after_expression += 1
    elif not saw_line_terminator:
        return False

    returned = _strip_leading_trivia(following[after_expression:])
    return _return_object_forwards_binding(returned, resolve.group("resolved"))


def _resolves_absolute_separately_and_searches_path(resolve_executable_body: str) -> bool:
    absolute_branch = _conditional_branch(resolve_executable_body, _ABSOLUTE_CONDITION_RE)
    if absolute_branch is None:
        return False
    non_absolute = _blank_out(resolve_executable_body, absolute_branch)
    return bool(_PATH_LOOKUP_RE.search(non_absolute))


def _proves_published_058_prepare_execution(
    prepare_execution_body: str,
) -> bool:
    if not prepare_execution_body.startswith("{"):
        return False
    following = _strip_leading_trivia(prepare_execution_body[1:-1])

    sandbox_manager = _PUBLISHED_058_SANDBOX_MANAGER_RE.match(following)
    if sandbox_manager is None:
        return False
    following = _strip_leading_trivia(following[sandbox_manager.end() :])

    windows_declaration = _PUBLISHED_058_WINDOWS_RE.match(following)
    if windows_declaration is None:
        return False
    windows_binding = windows_declaration.group("windows")
    following = _strip_leading_trivia(following[windows_declaration.end() :])

    strict_declaration = _PUBLISHED_058_STRICT_RE.match(following)
    if strict_declaration is None:
        return False
    strict_expression = strict_declaration.group("expression").strip()
    if (
        re.fullmatch(
            rf"{re.escape(windows_binding)}\s*&&\s*"
            r"shellExecutionConfig\s*\.\s*sandboxConfig\s*\?\s*\.\s*enabled\s*"
            r"&&\s*shellExecutionConfig\s*\.\s*sandboxConfig\s*\?\s*\.\s*"
            r"command\s*===\s*(['\"])windows-native\1\s*&&\s*!\s*"
            r"shellExecutionConfig\s*\.\s*sandboxConfig\s*\?\s*\.\s*"
            r"networkAccess",
            strict_expression,
        )
        is None
    ):
        return False
    strict_binding = strict_declaration.group("strict")
    following = _strip_leading_trivia(following[strict_declaration.end() :])

    destructure = _DESTRUCTURE_RE.match(following)
    if destructure is None:
        return False
    destructured_properties = _top_level_object_properties(
        "{" + destructure.group("properties") + "}", 0
    )
    if destructured_properties is None:
        return False
    property_tokens = [_property_tokens(item) for item in destructured_properties]
    if property_tokens != [["executable"], ["argsPrefix"], ["shell"]]:
        return False
    following = _strip_leading_trivia(following[destructure.end() :])

    guard_pattern = re.compile(
        _PUBLISHED_058_GUARD_RE_TEMPLATE.format(strict=re.escape(strict_binding))
    )
    guard = guard_pattern.match(following)
    if guard is None:
        return False
    windows_only_branch = _match_brace_scope(following, guard.end() - 1)
    if windows_only_branch is None or not _PUBLISHED_058_CMD_BRANCH_RE.fullmatch(
        windows_only_branch
    ):
        return False
    outside_windows_guard = _blank_out(prepare_execution_body, windows_only_branch)
    if _CMD_EXECUTABLE_WRITE_RE.search(outside_windows_guard):
        return False
    branch_end = guard.end() - 1 + len(windows_only_branch)
    following = _strip_leading_trivia(following[branch_end:])

    resolve = _resolve_statement_pattern("executable").match(following)
    if resolve is None or resolve.group("resolved") != "resolvedExecutable":
        return False
    terminator = _trivia_end(following, resolve.end())
    if terminator is None:
        return False
    semicolon_index = terminator[0]
    if semicolon_index == len(following) or following[semicolon_index] != ";":
        return False
    remaining = following[semicolon_index + 1 :]

    prepare_call_openings = _top_level_member_call_openings(
        remaining, "sandboxManager", "prepareCommand"
    )
    if prepare_call_openings is None or len(prepare_call_openings) != 1:
        return False
    prepare_assignments = _top_level_matches(
        remaining, _PUBLISHED_058_PREPARE_COMMAND_ASSIGNMENT_RE
    )
    if len(prepare_assignments) != 1:
        return False
    prepare_call_opening = prepare_call_openings[0]
    prepare_assignment = prepare_assignments[0]
    if prepare_assignment.end() - 1 != prepare_call_opening:
        return False
    if re.search(
        r"(?<![\w$])resolvedExecutable(?![\w$])",
        remaining[: prepare_assignment.start()],
    ):
        return False
    call_scope = _match_paren_scope(remaining, prepare_call_opening)
    if call_scope is None:
        return False
    arguments = _strip_leading_trivia(call_scope[1:-1])
    if not arguments.startswith("{"):
        return False
    command_object = _match_brace_scope(arguments, 0)
    if command_object is None:
        return False
    if _strip_leading_trivia(arguments[len(command_object) :]):
        return False
    return _object_forwards_unique_binding(command_object, "command", "resolvedExecutable")


def _proves_published_058_interception_chain(text: str) -> bool:
    return any(
        _proves_published_058_prepare_execution(body)
        for body in _find_published_058_prepare_bodies(text)
    )


def _proves_shell_interception_chain(text: str) -> bool:
    link1 = any(
        _returns_bare_bash_outside_windows(body)
        for body in _find_unit_bodies(text, _GET_SHELL_CONFIGURATION)
    )
    if not link1:
        return False
    historical_link2 = any(
        _destructures_then_resolves_executable(body)
        for body in _find_unit_bodies(text, _PREPARE_SHELL_EXECUTION)
    )
    published_058_link2 = _proves_published_058_interception_chain(text)
    if not historical_link2 and not published_058_link2:
        return False
    return any(
        _resolves_absolute_separately_and_searches_path(body)
        for body in _find_unit_bodies(text, _RESOLVE_EXECUTABLE)
    )


def _core_version(version: str) -> tuple[int, int, int]:
    core = version.split("+", 1)[0].split("-", 1)[0]
    major, minor, patch = core.split(".")
    return int(major), int(minor), int(patch)


def _help_options(help_text: str) -> set[str]:
    options: set[str] = set()
    for line in help_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        # Only the synopsis column (before the first 2+ space gap that
        # separates it from the description) counts; a flag name mentioned
        # in another option's description text must not register as present.
        synopsis = re.split(r"\s{2,}", stripped, maxsplit=1)[0]
        options.update(_HELP_OPTION_RE.findall(synopsis))
    return options


class GeminiResolver:
    def __init__(
        self,
        cache_root: Path,
        *,
        npm_executable: str = "npm",
        node_executable: str = "node",
    ) -> None:
        self.cache_root = cache_root
        self.npm_executable = npm_executable
        self.node_executable = node_executable

    def _scrubbed_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        for name in _SCRUBBED_ENV_VARS:
            environment.pop(name, None)
        return environment

    def _npm_probe_environment(self) -> dict[str, str]:
        # npm view/install/ci scrub Gemini/Google credential state but keep
        # the caller's ambient HOME/USERPROFILE and npm config untouched, so
        # existing npm cache/registry/auth configuration keeps working.
        return self._scrubbed_environment()

    def _node_probe_environment(self) -> dict[str, str]:
        # node/Gemini version and help probes run against an isolated,
        # actually-created probe home so they can never read/write the
        # caller's real home directory (e.g. ~/.gemini, ~/.npmrc).
        environment = self._scrubbed_environment()
        probe_home = self.cache_root / ".gemini-probe-home"
        probe_home.mkdir(parents=True, exist_ok=True)
        environment["HOME"] = str(probe_home)
        environment["USERPROFILE"] = str(probe_home)
        return environment

    def latest_version(self) -> str:
        result = run_process(
            [self.npm_executable, "view", _PACKAGE_NAME, "version"],
            env=self._npm_probe_environment(),
            timeout_seconds=30,
        )
        if result.timed_out:
            raise GeminiResolveError(result.stderr.strip() or "registry timeout")
        if result.exit_code != 0:
            raise GeminiResolveError(
                result.stderr.strip() or "failed to resolve Gemini latest version"
            )
        version = result.stdout.strip()
        if not _STABLE_VERSION_RE.fullmatch(version):
            raise GeminiResolveError(f"unexpected Gemini version from npm: {version!r}")
        return version

    def stable_versions(self) -> tuple[str, ...]:
        result = run_process(
            [self.npm_executable, "view", _PACKAGE_NAME, "versions", "--json"],
            env=self._npm_probe_environment(),
            timeout_seconds=30,
        )
        if result.timed_out:
            raise GeminiResolveError(result.stderr.strip() or "registry timeout")
        if result.exit_code != 0:
            raise GeminiResolveError(result.stderr.strip() or "failed to resolve Gemini versions")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise GeminiResolveError("unexpected Gemini versions from npm") from exc
        if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
            raise GeminiResolveError("unexpected Gemini versions from npm")

        stable = {item for item in payload if _STABLE_VERSION_RE.fullmatch(item)}
        return tuple(sorted(stable, key=_core_version))

    def _check_host_node_version(self) -> None:
        result = run_process(
            [self.node_executable, "--version"],
            env=self._node_probe_environment(),
            timeout_seconds=10,
        )
        if result.timed_out or result.exit_code != 0:
            raise GeminiResolveError(
                result.stderr.strip() or "failed to inspect host Node.js version"
            )
        raw = result.stdout.strip()
        match = re.match(r"^v?(\d+)\.", raw)
        if match is None:
            raise GeminiResolveError(f"unexpected host Node.js version output: {raw!r}")
        if int(match.group(1)) < _MIN_HOST_NODE_MAJOR:
            raise GeminiResolveError(
                f"Gemini resolver requires Node.js >={_MIN_HOST_NODE_MAJOR}, host reported {raw!r}"
            )

    def _install_package(self, prefix: Path, version: str) -> None:
        prefix.mkdir(parents=True, exist_ok=True)
        manifest = {"name": "qualock-gemini-cache", "private": True, "version": "0.0.0"}
        (prefix / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
        env = self._npm_probe_environment()

        install_result = run_process(
            [
                self.npm_executable,
                "install",
                "--package-lock-only",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                f"{_PACKAGE_NAME}@{version}",
            ],
            cwd=prefix,
            env=env,
            timeout_seconds=180,
        )
        if install_result.timed_out or install_result.exit_code != 0:
            raise GeminiResolveError(
                install_result.stderr.strip() or "failed to pin Gemini CLI dependency"
            )

        ci_result = run_process(
            [
                self.npm_executable,
                "ci",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--omit=dev",
                "--omit=optional",
            ],
            cwd=prefix,
            env=env,
            timeout_seconds=180,
        )
        if ci_result.timed_out or ci_result.exit_code != 0:
            raise GeminiResolveError(
                ci_result.stderr.strip() or "failed to install Gemini CLI dependency"
            )

    def _read_package_entrypoint(self, package_json_path: Path, version: str) -> Path:
        try:
            payload = json.loads(package_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GeminiResolveError("malformed Gemini package metadata") from exc
        if not isinstance(payload, dict):
            raise GeminiResolveError("malformed Gemini package metadata")

        pkg_version = payload.get("version")
        if pkg_version != version:
            raise GeminiResolveError(
                f"installed package version {pkg_version!r} does not match "
                f"pinned version {version!r}"
            )

        bin_field = payload.get("bin")
        bin_gemini = bin_field.get("gemini") if isinstance(bin_field, dict) else None
        if not isinstance(bin_gemini, str) or not bin_gemini:
            raise GeminiResolveError("Gemini package metadata missing bin.gemini entrypoint")

        package_root = package_json_path.parent.absolute()
        relative_entrypoint = Path(bin_gemini)
        if (
            relative_entrypoint.is_absolute()
            or ".." in relative_entrypoint.parts
            or not relative_entrypoint.parts
        ):
            raise GeminiResolveError(
                f"Gemini bin.gemini entrypoint {bin_gemini!r} escapes package root"
            )
        entrypoint = package_root / relative_entrypoint
        if entrypoint.is_symlink() or not entrypoint.is_file():
            raise GeminiResolveError(f"Gemini entrypoint not found: {entrypoint}")
        return entrypoint

    @staticmethod
    def _fingerprint_package_tree(package_root: Path) -> str:
        try:
            return fingerprint_support_tree(package_root)
        except AgentSupportIntegrityError as exc:
            raise GeminiResolveError(f"Gemini support tree is invalid: {exc}") from exc

    def _validate_shell_contract(self, package_root: Path) -> None:
        for js_path in sorted(package_root.rglob("*.js")):
            if js_path.is_symlink() or not js_path.is_file():
                continue
            try:
                text = js_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if _proves_shell_interception_chain(text):
                return
        raise GeminiResolveError(
            "Gemini package bundle does not prove PATH-resolved bash shell interception contract"
        )

    def _validate_binary_contract(self, entrypoint: Path, package_root: Path, version: str) -> None:
        env = self._node_probe_environment()

        version_result = run_process(
            [self.node_executable, str(entrypoint), "--version"],
            env=env,
            timeout_seconds=15,
        )
        if version_result.timed_out or version_result.exit_code != 0:
            raise GeminiResolveError(
                version_result.stderr.strip() or "failed to inspect Gemini CLI version"
            )
        reported = version_result.stdout.strip().split(maxsplit=1)[0:1]
        reported_version = reported[0] if reported else ""
        if reported_version != version:
            raise GeminiResolveError(
                f"Gemini CLI binary reported version {reported_version!r} but requested {version!r}"
            )

        help_result = run_process(
            [self.node_executable, str(entrypoint), "--help"],
            env=env,
            timeout_seconds=15,
        )
        if help_result.timed_out or help_result.exit_code != 0:
            raise GeminiResolveError(
                help_result.stderr.strip() or "failed to inspect Gemini CLI help contract"
            )
        help_text = f"{help_result.stdout}\n{help_result.stderr}"
        options = _help_options(help_text)
        for flag in _REQUIRED_CLI_FLAGS:
            if flag not in options:
                raise GeminiResolveError(f"Gemini CLI missing required CLI flag {flag}")

        self._validate_shell_contract(package_root)

    def resolve(self, requested_version: str) -> AgentBinary:
        version = self.latest_version() if requested_version == "latest" else requested_version
        if not _STABLE_VERSION_RE.fullmatch(version):
            raise GeminiResolveError(f"Gemini version must be exact stable X.Y.Z: {version!r}")
        if _core_version(version) < _MIN_VALIDATED_VERSION:
            raise GeminiResolveError(
                "QuaLock requires Gemini CLI >= 0.58.0 for the validated agent contract"
            )

        self._check_host_node_version()

        prefix = self.cache_root / "agents" / "gemini" / version
        package_root = prefix / "node_modules" / "@google" / "gemini-cli"
        package_json_path = package_root / "package.json"

        if package_root.is_symlink():
            raise GeminiResolveError("Gemini support tree package root is a symlink")
        if not package_json_path.is_file():
            self._install_package(prefix, version)

        package_root = package_root.absolute()
        tree_before = self._fingerprint_package_tree(package_root)
        entrypoint = self._read_package_entrypoint(package_json_path, version)
        digest_before = hashlib.sha256(entrypoint.read_bytes()).hexdigest()

        self._validate_binary_contract(entrypoint, package_root, version)

        digest_after = hashlib.sha256(entrypoint.read_bytes()).hexdigest()
        if digest_after != digest_before:
            raise GeminiResolveError("Gemini executable changed during contract validation")
        tree_after = self._fingerprint_package_tree(package_root)
        if tree_after != tree_before:
            raise GeminiResolveError("Gemini support tree changed during contract validation")

        return AgentBinary(
            name="gemini",
            version=version,
            path=entrypoint,
            sha256=digest_after,
            support_trees=(
                AgentSupportTree(
                    root=package_root,
                    sha256=tree_after,
                    container_root="/opt/qualock/gemini-package",
                ),
            ),
        )
