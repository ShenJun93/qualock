import hashlib
import json
import os
import re
from pathlib import Path

import pytest

import qualock.agents.gemini_resolver as gemini_resolver_module
from qualock.agents.gemini_resolver import GeminiResolveError, GeminiResolver
from qualock.run.process import ProcessResult

from ._platform_helpers import write_python_launcher

_MISSING = object()

_REQUIRED_FLAGS = (
    "--prompt",
    "--output-format",
    "--model",
    "--approval-mode",
    "--sandbox",
    "--extensions",
    "-e",
    "--version",
)


# Historical certified source chain. This is the exact reviewed Gemini
# shell-interception prototype source preserved in the adapter worktree
# (`/home/pacmap/qualock-gemini-adapter/tests/unit/test_gemini_resolver.py`,
# `_shell_contract_source`), reproduced here verbatim as the happy-path
# fixture. Every adversarial fixture below is this same source with exactly
# one structural mutation, so each RED case isolates one broken link of the
# certified chain:
#   1. getShellConfiguration returns a non-Windows configuration object whose
#      `executable` is the bare, PATH-resolvable "bash" (never "/bin/bash"),
#      with the Windows configuration in its own separate branch.
#   2. prepareShellExecution destructures `executable` directly out of
#      getShellConfiguration(), and the very next executable-affecting
#      statement forms `resolveExecutable(executable) ?? executable`.
#   3. resolveExecutable handles absolute executables in their own branch and
#      resolves everything else by searching process.env.PATH.
def _shell_contract_source(mutate: str | None = None) -> str:
    bash_literal = '"bash"'
    resolve_call = "resolveExecutable(executable)"
    resolve_fallback = "executable"
    path_lookup = 'process.env.PATH || process.env["PATH"]'
    prepare_name = "prepareShellExecution"
    prepare_prelude = ""
    obtain = "  let { executable, argsPrefix, shell } = getShellConfiguration();\n"
    between = ""
    resolve_terminator = ";\n"
    return_statement = (
        "  return { resolvedExecutable, argsPrefix, shell, commandToExecute };\n"
    )
    absolute_branch = (
        "  if (path.isAbsolute(exe)) {\n"
        "    return isExecutable(exe) ? exe : void 0;\n"
        "  }\n"
    )
    windows_branch = (
        "  if (isWindowsPlatform()) {\n"
        "    return {\n"
        '      executable: "powershell.exe",\n'
        '      argsPrefix: ["-NoProfile", "-Command"],\n'
        '      shell: "powershell"\n'
        "    };\n"
        "  }\n"
    )

    if mutate == "absolute_bash":
        bash_literal = '"/bin/bash"'
    elif mutate == "missing_resolve_call":
        resolve_call = "resolveExecutableUnsafe(executable)"
    elif mutate == "path_lookup_changed":
        path_lookup = '"/usr/bin:/bin"'
    elif mutate == "path_lookup_only_in_absolute_branch":
        path_lookup = '"/usr/bin:/bin"'
        absolute_branch = (
            "  if (path.isAbsolute(exe)) {\n"
            '    const absoluteEnv = process.env.PATH || process.env["PATH"];\n'
            "    return isExecutable(exe) ? exe : void 0;\n"
            "  }\n"
        )
    elif mutate == "no_absolute_branch":
        absolute_branch = ""
    elif mutate == "no_windows_branch":
        windows_branch = ""
    elif mutate == "generic_prepare_execution_name":
        prepare_name = "prepareExecution"
    elif mutate == "property_access_obtain":
        obtain = "  const shellConfig = getShellConfiguration();\n"
        resolve_call = "resolveExecutable(shellConfig.executable)"
        resolve_fallback = "shellConfig.executable"
    elif mutate == "forwards_other_value":
        resolve_call = 'resolveExecutable("/bin/bash")'
    elif mutate == "absolute_fallback":
        resolve_fallback = '"/bin/bash"'
    elif mutate == "plain_reassign_between":
        between = '  executable = "/bin/bash";\n'
    elif mutate == "destructuring_array_write_between":
        between = '  [executable] = ["/bin/bash"];\n'
    elif mutate == "destructuring_object_write_between":
        between = '  ({ executable } = { executable: "/bin/bash" });\n'
    elif mutate == "nested_block_write_between":
        between = "  if (isDebugShell()) {\n" '    executable = "/bin/bash";\n' "  }\n"
    elif mutate == "unrelated_statement_between":
        between = "  const attemptCount = 0;\n"
    elif mutate == "hardcoded_resolved_executable_return":
        return_statement = (
            '  return { resolvedExecutable: "/bin/bash", argsPrefix: [], shell: "bash",'
            " commandToExecute };\n"
        )
    elif mutate == "other_resolved_executable_return":
        prepare_prelude = '  const forcedExecutable = "/bin/bash";\n'
        return_statement = (
            "  return { resolvedExecutable: forcedExecutable, argsPrefix, shell,"
            " commandToExecute };\n"
        )
    elif mutate == "nested_resolved_executable_return_decoy":
        return_statement = (
            '  return { resolvedExecutable: "/bin/bash", metadata: { resolvedExecutable },'
            " argsPrefix, shell, commandToExecute };\n"
        )
    elif mutate == "dead_resolved_executable_return_decoy":
        return_statement = (
            '  return { resolvedExecutable: "/bin/bash", argsPrefix, shell,'
            " commandToExecute };\n"
            "  return { resolvedExecutable, argsPrefix, shell, commandToExecute };\n"
        )
    elif mutate == "live_return_before_dead_certified_sequence":
        prepare_prelude = (
            '  return { resolvedExecutable: "/bin/bash", argsPrefix: [], shell: "bash",'
            " commandToExecute };\n"
        )
    elif mutate == "spread_binding_is_not_property":
        return_statement = (
            "  return { ...resolvedExecutable, argsPrefix, shell, commandToExecute };\n"
        )
    elif mutate == "duplicate_resolved_executable_override":
        return_statement = (
            '  return { resolvedExecutable, resolvedExecutable: "/bin/bash", argsPrefix,'
            " shell, commandToExecute };\n"
        )
    elif mutate == "top_level_spread_may_override":
        return_statement = (
            "  return { resolvedExecutable, ...getOverrides(), argsPrefix, shell,"
            " commandToExecute };\n"
        )
    elif mutate == "return_object_after_line_terminator":
        return_statement = (
            "  return\n"
            "  { resolvedExecutable, argsPrefix, shell, commandToExecute };\n"
        )
    elif mutate == "semicolonless_resolve_declaration":
        resolve_terminator = "\n"
    elif mutate == "explicit_resolved_executable_return":
        return_statement = (
            "  return { resolvedExecutable: resolvedExecutable, argsPrefix, shell,"
            " commandToExecute };\n"
        )
    elif mutate is not None:
        raise ValueError(f"unknown shell contract mutation: {mutate!r}")

    return (
        '"use strict";\n'
        "function isWindowsPlatform() {\n"
        '  return os.platform() === "win32";\n'
        "}\n"
        "\n"
        "function resolveExecutable(exe) {\n"
        f"{absolute_branch}"
        f"  const pathEnv = {path_lookup};\n"
        "  if (!pathEnv) {\n"
        "    return void 0;\n"
        "  }\n"
        "  for (const dir of pathEnv.split(path.delimiter)) {\n"
        "    const candidate = path.join(dir, exe);\n"
        "    if (fs.existsSync(candidate)) {\n"
        "      return candidate;\n"
        "    }\n"
        "  }\n"
        "  return void 0;\n"
        "}\n"
        "\n"
        "function getShellConfiguration() {\n"
        f"{windows_branch}"
        "  return {\n"
        f"    executable: {bash_literal},\n"
        '    argsPrefix: ["-c"],\n'
        '    shell: "bash"\n'
        "  };\n"
        "}\n"
        "\n"
        f"function {prepare_name}(commandToExecute) {{\n"
        f"{prepare_prelude}"
        f"{obtain}"
        f"{between}"
        f"  const resolvedExecutable = {resolve_call} ?? {resolve_fallback}{resolve_terminator}"
        f"{return_statement}"
        "}\n"
    )


_SHELL_CONTRACT_JS = _shell_contract_source()

# Same certified chain, differing only in quote style, indentation, line
# wrapping, and an interleaved comment between the destructure and the
# resolve statement -- all formatting-equivalent, so it must still be
# accepted.
_SHELL_CONTRACT_FORMATTING_VARIANT_JS = (
    "'use strict';\n"
    "function isWindowsPlatform() { return os.platform() === 'win32'; }\n"
    "function resolveExecutable(exe) {\n"
    "    if (path.isAbsolute(exe)) { return isExecutable(exe) ? exe : void 0; }\n"
    "    const pathEnv = process.env['PATH'];\n"
    "    if (!pathEnv) { return void 0; }\n"
    "    for (const dir of pathEnv.split(path.delimiter)) {\n"
    "        const candidate = path.join(dir, exe);\n"
    "        if (fs.existsSync(candidate)) { return candidate; }\n"
    "    }\n"
    "    return void 0;\n"
    "}\n"
    "function getShellConfiguration() {\n"
    "    if (isWindowsPlatform()) {\n"
    "        return { executable: 'powershell.exe', argsPrefix: ['-NoProfile'],"
    " shell: 'powershell' };\n"
    "    }\n"
    "    return { executable: 'bash', argsPrefix: ['-c'], shell: 'bash' };\n"
    "}\n"
    "function prepareShellExecution(commandToExecute) {\n"
    "    let { executable, argsPrefix, shell } = getShellConfiguration();\n"
    "    /* resolve through PATH, keeping the bare name as the fallback */\n"
    "    // upstream keeps the unresolved name when PATH lookup misses\n"
    "    const resolvedExecutable = resolveExecutable(executable) ?? executable;\n"
    "    return { resolvedExecutable, argsPrefix, shell, commandToExecute };\n"
    "}\n"
)

# Same certified chain expressed with class/static method shorthand and a
# qualified receiver in the caller -- also formatting-equivalent.
_SHELL_CONTRACT_STATIC_METHOD_JS = (
    "class ShellResolver {\n"
    "  static resolveExecutable(exe) {\n"
    "    if (path.isAbsolute(exe)) {\n"
    "      return isExecutable(exe) ? exe : void 0;\n"
    "    }\n"
    '    const pathEnv = process.env.PATH || process.env["PATH"];\n'
    "    for (const dir of pathEnv.split(path.delimiter)) {\n"
    "      const candidate = path.join(dir, exe);\n"
    "      if (fs.existsSync(candidate)) {\n"
    "        return candidate;\n"
    "      }\n"
    "    }\n"
    "    return void 0;\n"
    "  }\n"
    "\n"
    "  static getShellConfiguration() {\n"
    '    if (os.platform() === "win32") {\n'
    "      return {\n"
    '        executable: "powershell.exe",\n'
    '        argsPrefix: ["-NoProfile", "-Command"],\n'
    '        shell: "powershell"\n'
    "      };\n"
    "    }\n"
    "    return {\n"
    '      executable: "bash",\n'
    '      argsPrefix: ["-c"],\n'
    '      shell: "bash"\n'
    "    };\n"
    "  }\n"
    "\n"
    "  static prepareShellExecution(commandToExecute) {\n"
    "    let { executable, argsPrefix, shell } = ShellResolver.getShellConfiguration();\n"
    "    const resolvedExecutable = resolveExecutable(executable) ?? executable;\n"
    "    return { resolvedExecutable, argsPrefix, shell, commandToExecute };\n"
    "  }\n"
    "}\n"
)


# Synthetic, self-contained reproduction of the shell-execution structure in
# the published Gemini CLI 0.58.0 bundle.  It deliberately does not read the
# developer cache: the fixture is stable test input, while the cached artifact
# is reserved for the separate end-to-end verification.
def _published_058_shell_contract_source(mutate: str | None = None) -> str:
    method_prefix = "static async"
    parameters = (
        "commandToExecute, cwd, shellExecutionConfig, isInteractive, usingPty"
    )
    windows_declaration = 'const isWindows3 = os28.platform() === "win32";'
    strict_declaration = (
        "const isStrictSandbox = isWindows3 && "
        "shellExecutionConfig.sandboxConfig?.enabled && "
        'shellExecutionConfig.sandboxConfig?.command === "windows-native" && '
        "!shellExecutionConfig.sandboxConfig?.networkAccess;"
    )
    guard = "isStrictSandbox"
    guarded_executable_mutation = '      executable = "cmd.exe";\n'
    outside_executable_mutation = ""
    resolved_expression = "resolveExecutable(executable) ?? executable"
    after_resolved = ""
    return_statement = "return { sandboxedCommand, shell, isInteractive, usingPty };"
    command_properties = (
        "      command: resolvedExecutable,\n"
        "      args: spawnArgs,\n"
        "      env: baseEnv,\n"
        "      cwd\n"
    )
    free_function = False

    if mutate == "generic_free_function":
        method_prefix = "async function"
        free_function = True
    elif mutate == "non_static_method":
        method_prefix = "async"
    elif mutate == "non_async_method":
        method_prefix = "static"
    elif mutate == "wrong_parameter_signature":
        parameters = "commandToExecute, cwd, shellExecutionConfig, usingPty"
    elif mutate == "windows_not_derived_from_platform":
        windows_declaration = "const isWindows3 = isWindows();"
    elif mutate == "strict_sandbox_omits_windows_flag":
        strict_declaration = (
            "const isStrictSandbox = shellExecutionConfig.sandboxConfig?.enabled;"
        )
    elif mutate == "guard_does_not_use_strict_sandbox":
        guard = "shellExecutionConfig.sandboxConfig?.enabled"
    elif mutate == "cmd_mutation_outside_windows_guard":
        guarded_executable_mutation = ""
        outside_executable_mutation = '    executable = "cmd.exe";\n'
    elif mutate == "resolved_expression_wrong_fallback":
        resolved_expression = 'resolveExecutable(executable) ?? "/bin/bash"'
    elif mutate == "resolved_expression_wrong_callee":
        resolved_expression = "resolveExecutableUnsafe(executable) ?? executable"
    elif mutate == "resolved_expression_wrong_argument":
        resolved_expression = 'resolveExecutable("bash") ?? executable'
    elif mutate == "resolved_binding_reassigned_before_prepare_command":
        after_resolved = '    resolvedExecutable = "/bin/bash";\n'
    elif mutate == "prepare_command_literal":
        command_properties = command_properties.replace(
            "command: resolvedExecutable", 'command: "/bin/bash"'
        )
    elif mutate == "prepare_command_other_binding":
        command_properties = command_properties.replace(
            "command: resolvedExecutable", "command: executable"
        )
    elif mutate == "prepare_command_spread_ambiguity":
        command_properties = command_properties.replace(
            "      args: spawnArgs,\n", "      ...commandOverrides,\n      args: spawnArgs,\n"
        )
    elif mutate == "prepare_command_duplicate_command":
        command_properties = command_properties.replace(
            "      args: spawnArgs,\n", '      command: "bash",\n      args: spawnArgs,\n'
        )
    elif mutate == "prepare_command_trailing_command_getter":
        command_properties = command_properties.replace(
            "      args: spawnArgs,\n",
            '      get command() { return "/bin/sh"; },\n      args: spawnArgs,\n',
        )
    elif mutate == "second_top_level_prepare_command_return":
        return_statement = (
            'return await sandboxManager.prepareCommand({ command: "/bin/sh" });'
        )
    elif mutate == "second_top_level_prepare_command_parenthesized":
        return_statement = (
            'return await (sandboxManager.prepareCommand({ command: "/bin/sh" }));'
        )
    elif mutate == "second_top_level_prepare_command_comment_trivia":
        return_statement = (
            "return await sandboxManager /* receiver */ . /* member */ "
            'prepareCommand /* call */ ({ command: "/bin/sh" });'
        )
    elif mutate is not None:
        raise ValueError(f"unknown published 0.58.0 contract mutation: {mutate!r}")

    method = (
        f"  {method_prefix} prepareExecution({parameters}) {{\n"
        "    const sandboxManager = shellExecutionConfig.sandboxManager ?? "
        "new NoopSandboxManager();\n"
        f"    {windows_declaration}\n"
        f"    {strict_declaration}\n"
        "    let { executable, argsPrefix, shell } = getShellConfiguration();\n"
        f"    if ({guard}) {{\n"
        '      shell = "cmd";\n'
        '      argsPrefix = ["/c"];\n'
        f"{guarded_executable_mutation}"
        "    }\n"
        f"{outside_executable_mutation}"
        f"    const resolvedExecutable = {resolved_expression};\n"
        f"{after_resolved}"
        "    const finalCommand = commandToExecute;\n"
        "    const spawnArgs = [...argsPrefix, finalCommand];\n"
        "    const baseEnv = shellExecutionConfig.env ?? process.env;\n"
        "    const sandboxedCommand = await sandboxManager.prepareCommand({\n"
        f"{command_properties}"
        "    });\n"
        f"    {return_statement}\n"
        "  }\n"
    )
    method_container = method[2:] if free_function else "class ShellExecutionService {\n" + method + "}\n"

    return (
        "function resolveExecutable(exe) {\n"
        "  if (path.isAbsolute(exe)) {\n"
        "    return isExecutable(exe) ? exe : void 0;\n"
        "  }\n"
        '  const pathEnv = process.env["PATH"];\n'
        "  return searchPath(pathEnv, exe);\n"
        "}\n"
        "function getShellConfiguration() {\n"
        "  if (isWindows()) {\n"
        '    return { executable: "powershell.exe", argsPrefix: [], shell: "powershell" };\n'
        "  }\n"
        '  return { executable: "bash", argsPrefix: ["-c"], shell: "bash" };\n'
        "}\n"
        f"{method_container}"
    )


_PUBLISHED_058_SHELL_CONTRACT_JS = _published_058_shell_contract_source()

_NO_CONTRACT_JS = "function noop() { return 1; }\n"

# The literal historical counterexample from the scoped fix-round-1 review: a
# single generically-named function's Linux branch genuinely returns the
# absolute '/bin/bash', while its own non-Linux branch assigns bare 'bash'
# and passes it to a PATH-searching callee. Fix round 1's function-scoped
# (rather than branch-scoped or named-unit) heuristic wrongly proved this
# bundle PATH-resolved. None of the certified named units are present, so the
# certified-chain validator rejects it outright.
_SHELL_CONTRACT_HISTORICAL_GENERIC_NAME_JS = (
    "function pickShell() {\n"
    "  if (platform() === 'linux') {\n"
    "    return '/bin/bash';\n"
    "  }\n"
    "  var shellCommand = 'bash';\n"
    "  return resolveExecutable(shellCommand);\n"
    "}\n"
    "\n"
    "function resolveExecutable(shellCommand) {\n"
    "  var searchPaths = process.env.PATH.split(':');\n"
    "  return searchPaths[0] + '/' + shellCommand;\n"
    "}\n"
)

# Adversarial: the generic tokens ('linux', bare 'bash', process.env.PATH) all
# appear in the file, and even a function literally named resolveExecutable
# performs a PATH search, but no getShellConfiguration or
# prepareShellExecution unit exists at all -- the certified chain cannot be
# proven from token soup alone.
_SHELL_CONTRACT_UNRELATED_COOCCURRENCE_JS = (
    "function chooseShell() {\n"
    "  if (platform() === 'linux') {\n"
    "    return '/bin/bash';\n"
    "  }\n"
    "  return null;\n"
    "}\n"
    "\n"
    "function shellCompletionNames() {\n"
    "  var names = ['bash', 'zsh', 'fish'];\n"
    "  return names;\n"
    "}\n"
    "\n"
    "function resolveExecutable(name) {\n"
    "  var dirs = process.env.PATH.split(':');\n"
    "  return dirs[0] + '/' + name;\n"
    "}\n"
)

# Adversarial: the pre-round-4 generic dataflow shape that three review rounds
# repeatedly bypassed -- a mutable `prepareExecution` local obtained from
# getShellConfiguration and later handed to resolveExecutable. It is not the
# certified historical structure (no configuration object, no destructure, no
# `?? executable` fallback, generic unit name), so it must fail closed rather
# than be re-certified by a generic co-occurrence or `prepareExecution`
# fallback path.
# Adversarial: the two exact fix-round-3 review reproductions that the
# pre-round-4 generic dataflow scanner wrongly certified. Both are the
# generic `prepareExecution` shape whose tracked executable is overwritten
# with the absolute '/bin/bash' before it reaches resolveExecutable -- once
# through a destructuring-assignment target, once through a plain
# reassignment nested inside an `if` block. Neither is the certified
# historical structure, so both must fail closed.
_SHELL_CONTRACT_FIX3_DESTRUCTURING_WRITE_JS = (
    "function getShellConfiguration() {\n"
    "  if (platform() === 'linux') {\n"
    "    var shellCommand = 'bash';\n"
    "    return shellCommand;\n"
    "  }\n"
    "  return 'cmd.exe';\n"
    "}\n"
    "\n"
    "function prepareExecution() {\n"
    "  var executable = getShellConfiguration();\n"
    "  [executable] = ['/bin/bash'];\n"
    "  return resolveExecutable(executable);\n"
    "}\n"
    "\n"
    "function resolveExecutable(executable) {\n"
    "  var searchPaths = process.env.PATH.split(':');\n"
    "  return searchPaths[0] + '/' + executable;\n"
    "}\n"
)

_SHELL_CONTRACT_FIX3_NESTED_BLOCK_WRITE_JS = (
    "function getShellConfiguration() {\n"
    "  if (platform() === 'linux') {\n"
    "    var shellCommand = 'bash';\n"
    "    return shellCommand;\n"
    "  }\n"
    "  return 'cmd.exe';\n"
    "}\n"
    "\n"
    "function prepareExecution() {\n"
    "  var executable = getShellConfiguration();\n"
    "  if (true) {\n"
    "    executable = '/bin/bash';\n"
    "  }\n"
    "  return resolveExecutable(executable);\n"
    "}\n"
    "\n"
    "function resolveExecutable(executable) {\n"
    "  var searchPaths = process.env.PATH.split(':');\n"
    "  return searchPaths[0] + '/' + executable;\n"
    "}\n"
)

_SHELL_CONTRACT_GENERIC_DATAFLOW_JS = (
    "function getShellConfiguration() {\n"
    "  if (platform() === 'linux') {\n"
    "    var shellCommand = 'bash';\n"
    "    return shellCommand;\n"
    "  }\n"
    "  return 'cmd.exe';\n"
    "}\n"
    "\n"
    "function prepareExecution() {\n"
    "  var executable = getShellConfiguration();\n"
    "  return resolveExecutable(executable);\n"
    "}\n"
    "\n"
    "function resolveExecutable(executable) {\n"
    "  if (path.isAbsolute(executable)) {\n"
    "    return executable;\n"
    "  }\n"
    "  var searchPaths = process.env.PATH.split(':');\n"
    "  return searchPaths[0] + '/' + executable;\n"
    "}\n"
)


# Adversarial: the exact fix-round-4 review reproduction. Links 1 and 3 are
# genuinely present, and the certified destructure/resolve pair does occur in
# prepareShellExecution's body text -- but only inside a dead `if (false)`
# block, whose local binding is never read. The function's real,
# always-executed flow hardcodes the absolute "/bin/bash" and returns it, so
# the pair proves nothing about what the unit actually forwards. Only a
# top-level-statement requirement on link 2 can reject this bundle.
_SHELL_CONTRACT_FIX4_DEAD_BRANCH_DECOY_JS = (
    "function getShellConfiguration() {\n"
    "  if (isWindowsPlatform()) {\n"
    '    return { executable: "powershell.exe", argsPrefix: ["-NoProfile"],'
    ' shell: "powershell" };\n'
    "  }\n"
    '  return { executable: "bash", argsPrefix: ["-c"], shell: "bash" };\n'
    "}\n"
    "\n"
    "function resolveExecutable(exe) {\n"
    "  if (path.isAbsolute(exe)) {\n"
    "    return isExecutable(exe) ? exe : void 0;\n"
    "  }\n"
    "  const pathEnv = process.env.PATH;\n"
    "  return pathEnv;\n"
    "}\n"
    "\n"
    "function prepareShellExecution(commandToExecute) {\n"
    "  if (false) {\n"
    "    let { executable } = getShellConfiguration();\n"
    "    const resolvedExecutable = resolveExecutable(executable) ?? executable;\n"
    "  }\n"
    '  const executable = "/bin/bash";\n'
    '  return { resolvedExecutable: executable, argsPrefix: [], shell: "bash",'
    " commandToExecute };\n"
    "}\n"
)

# Adversarial: the same dead-decoy bypass expressed as a never-called nested
# function declaration instead of an `if (false)` block -- the far more
# innocuous-looking shape in a real bundle (dead helper, feature-flagged
# variant, tree-shaking remnant). The certified pair again sits outside
# prepareShellExecution's own top-level statement flow while the live flow
# returns the absolute "/bin/bash".
_SHELL_CONTRACT_FIX4_NESTED_FUNCTION_DECOY_JS = (
    "function getShellConfiguration() {\n"
    "  if (isWindowsPlatform()) {\n"
    '    return { executable: "powershell.exe", argsPrefix: ["-NoProfile"],'
    ' shell: "powershell" };\n'
    "  }\n"
    '  return { executable: "bash", argsPrefix: ["-c"], shell: "bash" };\n'
    "}\n"
    "\n"
    "function resolveExecutable(exe) {\n"
    "  if (path.isAbsolute(exe)) {\n"
    "    return isExecutable(exe) ? exe : void 0;\n"
    "  }\n"
    "  const pathEnv = process.env.PATH;\n"
    "  return pathEnv;\n"
    "}\n"
    "\n"
    "function prepareShellExecution(commandToExecute) {\n"
    "  function neverCalled() {\n"
    "    let { executable } = getShellConfiguration();\n"
    "    const resolvedExecutable = resolveExecutable(executable) ?? executable;\n"
    "    return resolvedExecutable;\n"
    "  }\n"
    '  const executable = "/bin/bash";\n'
    '  return { resolvedExecutable: executable, argsPrefix: [], shell: "bash",'
    " commandToExecute };\n"
    "}\n"
)


def install_fake_package(
    prefix: Path,
    *,
    version: str,
    bin_gemini: object = "dist/gemini.js",
    entrypoint_relpath: str = "dist/gemini.js",
    entrypoint_bytes: bytes = b"// fake gemini entrypoint\n",
    include_shell_contract: bool = True,
    shell_contract_js: str | None = None,
    raw_package_json: str | None = None,
    symlink_target: Path | None = None,
) -> tuple[Path, Path]:
    package_root = prefix / "node_modules" / "@google" / "gemini-cli"
    package_root.mkdir(parents=True, exist_ok=True)
    package_json_path = package_root / "package.json"

    if raw_package_json is not None:
        package_json_path.write_text(raw_package_json, encoding="utf-8")
    else:
        payload: dict[str, object] = {"name": "@google/gemini-cli", "version": version}
        if bin_gemini is not None:
            payload["bin"] = {"gemini": bin_gemini}
        package_json_path.write_text(json.dumps(payload), encoding="utf-8")

    entrypoint = package_root / entrypoint_relpath
    entrypoint.parent.mkdir(parents=True, exist_ok=True)
    if symlink_target is not None:
        if entrypoint.exists() or entrypoint.is_symlink():
            entrypoint.unlink()
        os.symlink(symlink_target, entrypoint)
    else:
        entrypoint.write_bytes(entrypoint_bytes)

    contract = package_root / "dist" / "shell.js"
    contract.parent.mkdir(parents=True, exist_ok=True)
    if shell_contract_js is not None:
        contract.write_text(shell_contract_js, encoding="utf-8")
    elif include_shell_contract:
        contract.write_text(_SHELL_CONTRACT_JS, encoding="utf-8")
    else:
        contract.write_text(_NO_CONTRACT_JS, encoding="utf-8")

    return package_root, entrypoint


def make_fake_npm(
    path: Path,
    *,
    log_path: Path,
    latest_version: str = "0.58.0",
    fail_install: bool = False,
    fail_ci: bool = False,
) -> Path:
    source = (
        "import json\n"
        "import pathlib\n"
        "import sys\n"
        "import os\n"
        f"LOG_PATH = pathlib.Path({str(log_path)!r})\n"
        f"LATEST_VERSION = {latest_version!r}\n"
        f"SHELL_JS = {_SHELL_CONTRACT_JS!r}\n"
        "args = sys.argv[1:]\n"
        "with LOG_PATH.open('a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps({'argv': ['npm'] + args, 'env': dict(os.environ)}) + chr(10))\n"
        "if args[:3] == ['view', '@google/gemini-cli', 'version']:\n"
        "    print(LATEST_VERSION)\n"
        "    raise SystemExit(0)\n"
        "if args and args[0] == 'install':\n"
        f"    raise SystemExit({2 if fail_install else 0})\n"
        "if args and args[0] == 'ci':\n"
        f"    if {fail_ci!r}:\n"
        "        raise SystemExit(2)\n"
        "    cwd = pathlib.Path.cwd()\n"
        "    version = cwd.name\n"
        "    package_root = cwd / 'node_modules' / '@google' / 'gemini-cli'\n"
        "    package_root.mkdir(parents=True, exist_ok=True)\n"
        "    (package_root / 'package.json').write_text(json.dumps(\n"
        "        {'name': '@google/gemini-cli', 'version': version,"
        " 'bin': {'gemini': 'dist/gemini.js'}}\n"
        "    ))\n"
        "    entrypoint = package_root / 'dist' / 'gemini.js'\n"
        "    entrypoint.parent.mkdir(parents=True, exist_ok=True)\n"
        "    entrypoint.write_text('// fake gemini entrypoint for ' + version)\n"
        "    (package_root / 'dist' / 'shell.js').write_text(SHELL_JS)\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(2)\n"
    )
    return write_python_launcher(path, source)


def make_fake_node(
    path: Path,
    *,
    log_path: Path | None = None,
    host_version: str = "v22.9.0",
    cli_version: str = "0.58.0",
    missing_flag: str | None = None,
    prose_only_flags: tuple[str, ...] = (),
) -> Path:
    flags = [
        flag
        for flag in _REQUIRED_FLAGS
        if flag != missing_flag and flag not in prose_only_flags
    ]
    help_lines = [f"  {flag} <value>  test option" for flag in flags]
    if prose_only_flags:
        mentions = ", ".join(prose_only_flags)
        help_lines.append(
            f"  --unrelated <value>  see also {mentions} for related flags"
        )
    help_text = "\n".join(help_lines)
    log_line = (
        "if LOG_PATH:\n"
        "    with pathlib.Path(LOG_PATH).open('a', encoding='utf-8') as fh:\n"
        "        fh.write(json.dumps({'argv': ['node'] + args, 'env': dict(os.environ)}) + chr(10))\n"
        if log_path is not None
        else ""
    )
    source = (
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import sys\n"
        f"LOG_PATH = {str(log_path)!r}\n"
        "args = sys.argv[1:]\n"
        + log_line
        + f"HOST_VERSION = {host_version!r}\n"
        f"CLI_VERSION = {cli_version!r}\n"
        f"HELP_TEXT = {help_text!r}\n"
        "if args == ['--version']:\n"
        "    print(HOST_VERSION)\n"
        "    raise SystemExit(0)\n"
        "if len(args) == 2 and args[1] == '--version':\n"
        "    print(CLI_VERSION)\n"
        "    raise SystemExit(0)\n"
        "if len(args) == 2 and args[1] == '--help':\n"
        "    print(HELP_TEXT)\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(2)\n"
    )
    return write_python_launcher(path, source)


def test_resolves_exact_installed_bin_entrypoint(tmp_path: Path) -> None:
    log_path = tmp_path / "npm.log"
    fake_npm = make_fake_npm(tmp_path / "npm", log_path=log_path)
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        tmp_path / "cache", npm_executable=str(fake_npm), node_executable=str(fake_node)
    )

    binary = resolver.resolve("0.58.0")

    assert binary.name == "gemini"
    assert binary.version == "0.58.0"
    assert binary.path.name == "gemini.js"
    assert binary.sha256 == hashlib.sha256(binary.path.read_bytes()).hexdigest()


def test_latest_queries_npm_view_and_resolves_exact_stable_version(tmp_path: Path) -> None:
    log_path = tmp_path / "npm.log"
    fake_npm = make_fake_npm(tmp_path / "npm", log_path=log_path, latest_version="0.59.1")
    fake_node = make_fake_node(tmp_path / "node", cli_version="0.59.1")
    resolver = GeminiResolver(
        tmp_path / "cache", npm_executable=str(fake_npm), node_executable=str(fake_node)
    )

    binary = resolver.resolve("latest")

    assert binary.version == "0.59.1"
    assert "0.59.1" in binary.path.parts
    assert not (tmp_path / "cache" / "agents" / "gemini" / "latest").exists()


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (ProcessResult(None, "", "registry timeout", 30.0, True), "registry timeout"),
        (ProcessResult(1, "", "registry failed", 0.02, False), "registry failed"),
        (ProcessResult(0, "not-a-version\n", "", 0.01, False), "unexpected Gemini version"),
    ],
)
def test_latest_version_rejects_bad_registry_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: ProcessResult, message: str
) -> None:
    monkeypatch.setattr(
        gemini_resolver_module, "run_process", lambda *args, **kwargs: result
    )

    with pytest.raises(GeminiResolveError, match=message):
        GeminiResolver(tmp_path / "cache").latest_version()


def test_minimum_version_is_rejected_before_install(tmp_path: Path) -> None:
    with pytest.raises(GeminiResolveError, match="requires Gemini CLI >= 0.58.0"):
        GeminiResolver(
            tmp_path / "cache",
            npm_executable=str(tmp_path / "does-not-exist-npm"),
            node_executable=str(tmp_path / "does-not-exist-node"),
        ).resolve("0.57.0")


def test_host_node_below_v20_is_rejected(tmp_path: Path) -> None:
    fake_node = make_fake_node(tmp_path / "node", host_version="v19.4.0")
    resolver = GeminiResolver(
        tmp_path / "cache",
        npm_executable=str(tmp_path / "does-not-exist-npm"),
        node_executable=str(fake_node),
    )

    with pytest.raises(GeminiResolveError, match="requires Node.js >=20"):
        resolver.resolve("0.58.0")


def test_cached_binary_is_reused_without_npm(tmp_path: Path) -> None:
    log_path = tmp_path / "npm.log"
    fake_npm = make_fake_npm(tmp_path / "npm", log_path=log_path)
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        tmp_path / "cache", npm_executable=str(fake_npm), node_executable=str(fake_node)
    )
    first = resolver.resolve("0.58.0")

    resolver.npm_executable = str(tmp_path / "no-such-npm-binary")
    second = resolver.resolve("0.58.0")

    assert second == first


def test_cache_reuse_still_validates_pinned_package_version(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(prefix, version="0.99.9")
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        cache,
        npm_executable=str(tmp_path / "no-such-npm-binary"),
        node_executable=str(fake_node),
    )

    with pytest.raises(GeminiResolveError, match="does not match pinned version"):
        resolver.resolve("0.58.0")


def test_missing_bin_field_fails_closed(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(prefix, version="0.58.0", bin_gemini=None)
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        cache,
        npm_executable=str(tmp_path / "no-such-npm-binary"),
        node_executable=str(fake_node),
    )

    with pytest.raises(GeminiResolveError, match="missing bin.gemini entrypoint"):
        resolver.resolve("0.58.0")


def test_non_string_bin_gemini_fails_closed(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(prefix, version="0.58.0", bin_gemini=123)
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        cache,
        npm_executable=str(tmp_path / "no-such-npm-binary"),
        node_executable=str(fake_node),
    )

    with pytest.raises(GeminiResolveError, match="missing bin.gemini entrypoint"):
        resolver.resolve("0.58.0")


def test_symlink_escape_entrypoint_is_rejected(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    outside = tmp_path / "outside" / "secret.js"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("// outside package root\n", encoding="utf-8")
    install_fake_package(prefix, version="0.58.0", symlink_target=outside)
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        cache,
        npm_executable=str(tmp_path / "no-such-npm-binary"),
        node_executable=str(fake_node),
    )

    with pytest.raises(GeminiResolveError, match="escapes package root"):
        resolver.resolve("0.58.0")


def test_malformed_package_json_fails_closed(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(prefix, version="0.58.0", raw_package_json="not-json{")
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        cache,
        npm_executable=str(tmp_path / "no-such-npm-binary"),
        node_executable=str(fake_node),
    )

    with pytest.raises(GeminiResolveError, match="malformed Gemini package metadata"):
        resolver.resolve("0.58.0")


def test_npm_install_failure_fails_closed(tmp_path: Path) -> None:
    log_path = tmp_path / "npm.log"
    fake_npm = make_fake_npm(tmp_path / "npm", log_path=log_path, fail_install=True)
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        tmp_path / "cache", npm_executable=str(fake_npm), node_executable=str(fake_node)
    )

    with pytest.raises(GeminiResolveError, match="failed to pin Gemini CLI dependency"):
        resolver.resolve("0.58.0")


def test_npm_ci_failure_fails_closed(tmp_path: Path) -> None:
    log_path = tmp_path / "npm.log"
    fake_npm = make_fake_npm(tmp_path / "npm", log_path=log_path, fail_ci=True)
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        tmp_path / "cache", npm_executable=str(fake_npm), node_executable=str(fake_node)
    )

    with pytest.raises(GeminiResolveError, match="failed to install Gemini CLI dependency"):
        resolver.resolve("0.58.0")


def test_reported_cli_version_must_match_requested_version(tmp_path: Path) -> None:
    log_path = tmp_path / "npm.log"
    fake_npm = make_fake_npm(tmp_path / "npm", log_path=log_path)
    fake_node = make_fake_node(tmp_path / "node", cli_version="0.58.1")
    resolver = GeminiResolver(
        tmp_path / "cache", npm_executable=str(fake_npm), node_executable=str(fake_node)
    )

    with pytest.raises(GeminiResolveError, match="reported version"):
        resolver.resolve("0.58.0")


@pytest.mark.parametrize("missing_flag", _REQUIRED_FLAGS)
def test_help_must_advertise_required_flags(tmp_path: Path, missing_flag: str) -> None:
    log_path = tmp_path / "npm.log"
    fake_npm = make_fake_npm(tmp_path / "npm", log_path=log_path)
    fake_node = make_fake_node(tmp_path / "node", missing_flag=missing_flag)
    resolver = GeminiResolver(
        tmp_path / "cache", npm_executable=str(fake_npm), node_executable=str(fake_node)
    )

    with pytest.raises(
        GeminiResolveError, match=f"missing required CLI flag {missing_flag}"
    ):
        resolver.resolve("0.58.0")


@pytest.mark.parametrize("prose_flag", ("--model", "--sandbox", "-e"))
def test_help_prose_mentions_of_flags_do_not_count_as_options(
    tmp_path: Path, prose_flag: str
) -> None:
    log_path = tmp_path / "npm.log"
    fake_npm = make_fake_npm(tmp_path / "npm", log_path=log_path)
    fake_node = make_fake_node(tmp_path / "node", prose_only_flags=(prose_flag,))
    resolver = GeminiResolver(
        tmp_path / "cache", npm_executable=str(fake_npm), node_executable=str(fake_node)
    )

    with pytest.raises(
        GeminiResolveError, match=f"missing required CLI flag {re.escape(prose_flag)}"
    ):
        resolver.resolve("0.58.0")


def test_shell_interception_contract_accepts_published_058_static_async_shape(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(
        prefix,
        version="0.58.0",
        shell_contract_js=_PUBLISHED_058_SHELL_CONTRACT_JS,
    )
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        cache,
        npm_executable=str(tmp_path / "no-such-npm-binary"),
        node_executable=str(fake_node),
    )

    binary = resolver.resolve("0.58.0")

    assert binary.version == "0.58.0"


def test_published_058_rejects_trailing_command_getter_override(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(
        prefix,
        version="0.58.0",
        shell_contract_js=_published_058_shell_contract_source(
            "prepare_command_trailing_command_getter"
        ),
    )
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        cache,
        npm_executable=str(tmp_path / "no-such-npm-binary"),
        node_executable=str(fake_node),
    )

    with pytest.raises(
        GeminiResolveError, match="does not prove PATH-resolved bash shell interception"
    ):
        resolver.resolve("0.58.0")


def test_published_058_rejects_second_top_level_prepare_command_return(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(
        prefix,
        version="0.58.0",
        shell_contract_js=_published_058_shell_contract_source(
            "second_top_level_prepare_command_return"
        ),
    )
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        cache,
        npm_executable=str(tmp_path / "no-such-npm-binary"),
        node_executable=str(fake_node),
    )

    with pytest.raises(
        GeminiResolveError, match="does not prove PATH-resolved bash shell interception"
    ):
        resolver.resolve("0.58.0")


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(
            "second_top_level_prepare_command_parenthesized",
            id="parenthesized-call",
        ),
        pytest.param(
            "second_top_level_prepare_command_comment_trivia",
            id="comment-separated-member-access",
        ),
    ],
)
def test_published_058_rejects_obscured_second_top_level_prepare_command(
    tmp_path: Path, mutation: str
) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(
        prefix,
        version="0.58.0",
        shell_contract_js=_published_058_shell_contract_source(mutation),
    )
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        cache,
        npm_executable=str(tmp_path / "no-such-npm-binary"),
        node_executable=str(fake_node),
    )

    with pytest.raises(
        GeminiResolveError, match="does not prove PATH-resolved bash shell interception"
    ):
        resolver.resolve("0.58.0")


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param("generic_free_function", id="generic-free-function-is-not-method"),
        pytest.param("non_static_method", id="method-must-be-static"),
        pytest.param("non_async_method", id="method-must-be-async"),
        pytest.param("wrong_parameter_signature", id="exact-five-parameter-signature"),
        pytest.param(
            "windows_not_derived_from_platform",
            id="windows-flag-must-be-derived-from-platform-equals-win32",
        ),
        pytest.param(
            "strict_sandbox_omits_windows_flag",
            id="strict-sandbox-must-causally-include-windows-flag",
        ),
        pytest.param(
            "guard_does_not_use_strict_sandbox",
            id="cmd-mutation-guard-must-use-strict-sandbox-binding",
        ),
        pytest.param(
            "cmd_mutation_outside_windows_guard",
            id="cmd-mutation-outside-windows-only-guard",
        ),
        pytest.param(
            "resolved_expression_wrong_fallback",
            id="resolved-executable-expression-must-be-exact",
        ),
        pytest.param(
            "resolved_expression_wrong_callee",
            id="resolved-executable-callee-must-be-exact",
        ),
        pytest.param(
            "resolved_expression_wrong_argument",
            id="resolved-executable-argument-must-be-exact",
        ),
        pytest.param(
            "resolved_binding_reassigned_before_prepare_command",
            id="resolved-executable-binding-must-flow-unchanged",
        ),
        pytest.param(
            "prepare_command_literal",
            id="prepare-command-rejects-literal-command",
        ),
        pytest.param(
            "prepare_command_other_binding",
            id="prepare-command-rejects-other-command-binding",
        ),
        pytest.param(
            "prepare_command_spread_ambiguity",
            id="prepare-command-rejects-top-level-spread",
        ),
        pytest.param(
            "prepare_command_duplicate_command",
            id="prepare-command-rejects-duplicate-command",
        ),
    ],
)
def test_shell_interception_contract_rejects_published_058_shape_drift(
    tmp_path: Path, mutation: str
) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(
        prefix,
        version="0.58.0",
        shell_contract_js=_published_058_shell_contract_source(mutation),
    )
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        cache,
        npm_executable=str(tmp_path / "no-such-npm-binary"),
        node_executable=str(fake_node),
    )

    with pytest.raises(
        GeminiResolveError, match="does not prove PATH-resolved bash shell interception"
    ):
        resolver.resolve("0.58.0")


def test_shell_interception_contract_rejects_historical_counterexample(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(
        prefix, version="0.58.0", shell_contract_js=_SHELL_CONTRACT_HISTORICAL_GENERIC_NAME_JS
    )
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        cache,
        npm_executable=str(tmp_path / "no-such-npm-binary"),
        node_executable=str(fake_node),
    )

    with pytest.raises(
        GeminiResolveError, match="does not prove PATH-resolved bash shell interception"
    ):
        resolver.resolve("0.58.0")


def test_shell_interception_contract_missing_fails_closed(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(prefix, version="0.58.0", include_shell_contract=False)
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        cache,
        npm_executable=str(tmp_path / "no-such-npm-binary"),
        node_executable=str(fake_node),
    )

    with pytest.raises(
        GeminiResolveError, match="does not prove PATH-resolved bash shell interception"
    ):
        resolver.resolve("0.58.0")


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param("absolute_bash", id="link1a-non-windows-executable-is-absolute-bin-bash"),
        pytest.param("no_windows_branch", id="link1b-windows-branch-not-separate"),
        pytest.param(
            "property_access_obtain",
            id="link2a-executable-not-destructured-from-get-shell-configuration",
        ),
        pytest.param("missing_resolve_call", id="link2b-resolve-executable-never-called"),
        pytest.param("forwards_other_value", id="link2c-resolve-called-with-another-value"),
        pytest.param("absolute_fallback", id="link2d-fallback-replaced-with-absolute-bin-bash"),
        pytest.param("plain_reassign_between", id="link2e-plain-reassign-between-obtain-resolve"),
        pytest.param(
            "destructuring_array_write_between",
            id="link2f-array-destructuring-write-between-obtain-resolve",
        ),
        pytest.param(
            "destructuring_object_write_between",
            id="link2g-object-destructuring-write-between-obtain-resolve",
        ),
        pytest.param(
            "nested_block_write_between",
            id="link2h-nested-block-outer-write-between-obtain-resolve",
        ),
        pytest.param(
            "unrelated_statement_between",
            id="link2i-intervening-statement-between-obtain-resolve",
        ),
        pytest.param(
            "generic_prepare_execution_name",
            id="link2j-generic-prepare-execution-unit-name",
        ),
        pytest.param(
            "hardcoded_resolved_executable_return",
            id="link2k-computed-resolved-executable-ignored-by-return",
        ),
        pytest.param(
            "other_resolved_executable_return",
            id="link2l-return-forwards-other-binding",
        ),
        pytest.param(
            "nested_resolved_executable_return_decoy",
            id="link2m-return-has-only-nested-computed-binding-decoy",
        ),
        pytest.param(
            "dead_resolved_executable_return_decoy",
            id="link2n-return-has-only-dead-computed-binding-decoy",
        ),
        pytest.param(
            "live_return_before_dead_certified_sequence",
            id="review-c1-live-return-before-dead-certified-sequence",
        ),
        pytest.param(
            "spread_binding_is_not_property",
            id="review-c2a-spread-binding-is-not-property",
        ),
        pytest.param(
            "duplicate_resolved_executable_override",
            id="review-c2b-duplicate-property-overrides-certified-binding",
        ),
        pytest.param(
            "top_level_spread_may_override",
            id="review-c2c-top-level-spread-may-override-property",
        ),
        pytest.param(
            "return_object_after_line_terminator",
            id="review-c3-return-newline-triggers-asi",
        ),
        pytest.param("path_lookup_changed", id="link3a-path-lookup-replaced-with-fixed-list"),
        pytest.param(
            "path_lookup_only_in_absolute_branch",
            id="link3b-path-searched-only-for-absolute-executables",
        ),
        pytest.param("no_absolute_branch", id="link3c-absolute-executables-not-handled-separately"),
    ],
)
def test_shell_interception_contract_rejects_mutated_certified_chain(
    tmp_path: Path, mutation: str
) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(
        prefix, version="0.58.0", shell_contract_js=_shell_contract_source(mutation)
    )
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        cache,
        npm_executable=str(tmp_path / "no-such-npm-binary"),
        node_executable=str(fake_node),
    )

    with pytest.raises(
        GeminiResolveError, match="does not prove PATH-resolved bash shell interception"
    ):
        resolver.resolve("0.58.0")


@pytest.mark.parametrize(
    "shell_contract_js",
    [
        pytest.param(
            _SHELL_CONTRACT_UNRELATED_COOCCURRENCE_JS,
            id="adversarial-token-soup-without-certified-chain",
        ),
        pytest.param(
            _SHELL_CONTRACT_GENERIC_DATAFLOW_JS,
            id="adversarial-generic-prepare-execution-dataflow-shape",
        ),
        pytest.param(
            _SHELL_CONTRACT_FIX3_DESTRUCTURING_WRITE_JS,
            id="fix3-bypass-destructuring-assignment-write-before-forward",
        ),
        pytest.param(
            _SHELL_CONTRACT_FIX3_NESTED_BLOCK_WRITE_JS,
            id="fix3-bypass-nested-block-plain-reassign-before-forward",
        ),
        pytest.param(
            _SHELL_CONTRACT_FIX4_DEAD_BRANCH_DECOY_JS,
            id="fix4-bypass-dead-if-false-decoy-destructure-resolve-pair",
        ),
        pytest.param(
            _SHELL_CONTRACT_FIX4_NESTED_FUNCTION_DECOY_JS,
            id="fix4-bypass-never-called-nested-function-decoy-pair",
        ),
    ],
)
def test_shell_interception_contract_rejects_uncertified_source_shapes(
    tmp_path: Path, shell_contract_js: str
) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(prefix, version="0.58.0", shell_contract_js=shell_contract_js)
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        cache,
        npm_executable=str(tmp_path / "no-such-npm-binary"),
        node_executable=str(fake_node),
    )

    with pytest.raises(
        GeminiResolveError, match="does not prove PATH-resolved bash shell interception"
    ):
        resolver.resolve("0.58.0")


@pytest.mark.parametrize(
    "shell_contract_js",
    [
        pytest.param(_SHELL_CONTRACT_JS, id="preserved-historical-prototype-source"),
        pytest.param(
            _shell_contract_source("explicit_resolved_executable_return"),
            id="explicit-resolved-executable-property-source",
        ),
        pytest.param(
            _shell_contract_source("semicolonless_resolve_declaration"),
            id="review-minor-semicolonless-resolve-with-line-terminator",
        ),
        pytest.param(
            _SHELL_CONTRACT_FORMATTING_VARIANT_JS,
            id="formatting-and-quote-equivalent-source",
        ),
        pytest.param(
            _SHELL_CONTRACT_STATIC_METHOD_JS,
            id="class-static-method-shorthand-source",
        ),
    ],
)
def test_shell_interception_contract_accepts_certified_chain(
    tmp_path: Path, shell_contract_js: str
) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(prefix, version="0.58.0", shell_contract_js=shell_contract_js)
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        cache,
        npm_executable=str(tmp_path / "no-such-npm-binary"),
        node_executable=str(fake_node),
    )

    binary = resolver.resolve("0.58.0")

    assert binary.version == "0.58.0"


def test_binary_mutation_during_contract_validation_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(prefix, version="0.58.0")
    resolver = GeminiResolver(
        cache, npm_executable=str(tmp_path / "no-such-npm-binary"), node_executable="node"
    )

    def mutate_and_fail(entrypoint_path: Path, package_root: Path, version: str) -> None:
        entrypoint_path.write_bytes(b"mutated-after-fingerprint")

    monkeypatch.setattr(resolver, "_check_host_node_version", lambda: None)
    monkeypatch.setattr(resolver, "_validate_binary_contract", mutate_and_fail)

    with pytest.raises(GeminiResolveError, match="changed during contract validation"):
        resolver.resolve("0.58.0")


def test_install_commands_use_exact_pinned_flags(tmp_path: Path) -> None:
    log_path = tmp_path / "npm.log"
    fake_npm = make_fake_npm(tmp_path / "npm", log_path=log_path)
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        tmp_path / "cache", npm_executable=str(fake_npm), node_executable=str(fake_node)
    )

    resolver.resolve("0.58.0")

    calls = [json.loads(line) for line in log_path.read_text().splitlines()]
    install_call = next(c for c in calls if c["argv"][1] == "install")
    ci_call = next(c for c in calls if c["argv"][1] == "ci")

    install_flags = set(install_call["argv"][2:])
    assert {
        "--package-lock-only",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        "@google/gemini-cli@0.58.0",
    } <= install_flags

    ci_flags = set(ci_call["argv"][2:])
    assert {
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        "--omit=dev",
        "--omit=optional",
    } <= ci_flags


_SCRUBBED_VARS = (
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


def _set_scrubbed_and_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "secret-api-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "secret-google-key")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/creds.json")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT_ID", "some-project-id")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("GEMINI_CLI_HOME", "/tmp/gemini-home")
    monkeypatch.setenv("GEMINI_CLI_SYSTEM_SETTINGS_PATH", "/tmp/system-settings.json")
    monkeypatch.setenv("GEMINI_CLI_SYSTEM_DEFAULTS_PATH", "/tmp/system-defaults.json")
    monkeypatch.setenv("NPM_CONFIG_REGISTRY", "https://registry.example.com")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.com:8080")
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.example.com:8443")


def test_npm_probes_scrub_credentials_but_preserve_ambient_home_and_npm_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ambient_home = str(tmp_path / "ambient-home")
    ambient_userprofile = str(tmp_path / "ambient-userprofile")
    monkeypatch.setenv("HOME", ambient_home)
    monkeypatch.setenv("USERPROFILE", ambient_userprofile)
    _set_scrubbed_and_proxy_env(monkeypatch)

    log_path = tmp_path / "npm.log"
    fake_npm = make_fake_npm(tmp_path / "npm", log_path=log_path)
    fake_node = make_fake_node(tmp_path / "node")
    resolver = GeminiResolver(
        tmp_path / "cache", npm_executable=str(fake_npm), node_executable=str(fake_node)
    )

    resolver.resolve("0.58.0")

    npm_calls = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert npm_calls
    for call in npm_calls:
        env = call["env"]
        for name in _SCRUBBED_VARS:
            assert name not in env
        assert env.get("NPM_CONFIG_REGISTRY") == "https://registry.example.com"
        assert env.get("HTTP_PROXY") == "http://proxy.example.com:8080"
        assert env.get("HTTPS_PROXY") == "https://proxy.example.com:8443"
        # npm view/install/ci must keep the caller's ambient HOME/USERPROFILE
        # and npm config untouched, not redirect them into an isolated probe
        # home the way node probes do.
        assert env.get("HOME") == ambient_home
        assert env.get("USERPROFILE") == ambient_userprofile


def test_node_probes_scrub_credentials_and_use_isolated_created_probe_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ambient_home = str(tmp_path / "ambient-home")
    ambient_userprofile = str(tmp_path / "ambient-userprofile")
    monkeypatch.setenv("HOME", ambient_home)
    monkeypatch.setenv("USERPROFILE", ambient_userprofile)
    _set_scrubbed_and_proxy_env(monkeypatch)

    log_path = tmp_path / "npm.log"
    fake_npm = make_fake_npm(tmp_path / "npm", log_path=log_path)
    node_log_path = tmp_path / "node.log"
    fake_node = make_fake_node(tmp_path / "node", log_path=node_log_path)
    resolver = GeminiResolver(
        tmp_path / "cache", npm_executable=str(fake_npm), node_executable=str(fake_node)
    )

    resolver.resolve("0.58.0")

    node_calls = [json.loads(line) for line in node_log_path.read_text().splitlines()]
    assert node_calls
    for call in node_calls:
        env = call["env"]
        for name in _SCRUBBED_VARS:
            assert name not in env
        assert env.get("NPM_CONFIG_REGISTRY") == "https://registry.example.com"
        assert env.get("HTTP_PROXY") == "http://proxy.example.com:8080"
        assert env.get("HTTPS_PROXY") == "https://proxy.example.com:8443"
        probe_home = env.get("HOME")
        assert probe_home is not None
        assert probe_home != ambient_home
        assert env.get("USERPROFILE") == probe_home
        # The isolated probe home must actually exist on disk, not be a
        # dangling path handed to node/Gemini probes.
        assert Path(probe_home).is_dir()
