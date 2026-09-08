import hashlib
import json
import os
import re
from pathlib import Path

from qualock.run.process import run_process

from .base import AgentBinary


class GeminiResolveError(RuntimeError):
    pass


_PACKAGE_NAME = "@google/gemini-cli"

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
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
_PATH_LOOKUP_RE = re.compile(
    r"process\s*\.\s*env\s*(?:\.\s*PATH(?![\w$])|\[\s*(['\"])PATH\1\s*\])"
)
_DESTRUCTURE_RE = re.compile(
    r"(?:const|let|var)\s*\{(?P<properties>[^{}]*)\}\s*=\s*"
    rf"(?:[\w$]+\s*\.\s*)?{_GET_SHELL_CONFIGURATION}\s*\(\s*\)\s*;"
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
            literal.group(2)
            for literal in _EXECUTABLE_LITERAL_RE.finditer(returned_object)
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


def _proves_shell_interception_chain(text: str) -> bool:
    link1 = any(
        _returns_bare_bash_outside_windows(body)
        for body in _find_unit_bodies(text, _GET_SHELL_CONFIGURATION)
    )
    if not link1:
        return False
    link2 = any(
        _destructures_then_resolves_executable(body)
        for body in _find_unit_bodies(text, _PREPARE_SHELL_EXECUTION)
    )
    if not link2:
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
                f"Gemini resolver requires Node.js >={_MIN_HOST_NODE_MAJOR}, "
                f"host reported {raw!r}"
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

        package_root = package_json_path.parent.resolve()
        entrypoint = (package_root / bin_gemini).resolve()
        if entrypoint != package_root and not entrypoint.is_relative_to(package_root):
            raise GeminiResolveError(
                f"Gemini bin.gemini entrypoint {bin_gemini!r} escapes package root"
            )
        if not entrypoint.is_file():
            raise GeminiResolveError(f"Gemini entrypoint not found: {entrypoint}")
        return entrypoint

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
            "Gemini package bundle does not prove PATH-resolved bash shell "
            "interception contract"
        )

    def _validate_binary_contract(
        self, entrypoint: Path, package_root: Path, version: str
    ) -> None:
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
                f"Gemini CLI binary reported version {reported_version!r} "
                f"but requested {version!r}"
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
        if not _VERSION_RE.fullmatch(version):
            raise GeminiResolveError(f"invalid Gemini version: {version!r}")
        if _core_version(version) < _MIN_VALIDATED_VERSION:
            raise GeminiResolveError(
                "QuaLock requires Gemini CLI >= 0.58.0 for the validated agent contract"
            )

        self._check_host_node_version()

        prefix = self.cache_root / "agents" / "gemini" / version
        package_root = prefix / "node_modules" / "@google" / "gemini-cli"
        package_json_path = package_root / "package.json"

        if not package_json_path.is_file():
            self._install_package(prefix, version)

        entrypoint = self._read_package_entrypoint(package_json_path, version)
        digest_before = hashlib.sha256(entrypoint.read_bytes()).hexdigest()

        self._validate_binary_contract(entrypoint, package_root.resolve(), version)

        digest_after = hashlib.sha256(entrypoint.read_bytes()).hexdigest()
        if digest_after != digest_before:
            raise GeminiResolveError("Gemini executable changed during contract validation")

        return AgentBinary(name="gemini", version=version, path=entrypoint, sha256=digest_after)
