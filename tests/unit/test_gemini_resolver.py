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

# Happy-path fixture: the exact reviewed 0.58.0 shell-interception chain --
# getShellConfiguration selects bare, non-absolute 'bash' on Linux;
# prepareExecution obtains that value from getShellConfiguration and
# forwards it to resolveExecutable; resolveExecutable searches
# process.env.PATH.
_SHELL_CONTRACT_JS = (
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
    "  var searchPaths = process.env.PATH.split(':');\n"
    "  return searchPaths[0] + '/' + executable;\n"
    "}\n"
)

# Equivalent happy path expressed via class/static method shorthand, with a
# qualified receiver (`ShellResolver.getShellConfiguration()`) in the caller.
_SHELL_CONTRACT_STATIC_METHOD_JS = (
    "class ShellResolver {\n"
    "  static getShellConfiguration() {\n"
    "    if (platform() === 'linux') {\n"
    "      var shellCommand = 'bash';\n"
    "      return shellCommand;\n"
    "    }\n"
    "    return 'cmd.exe';\n"
    "  }\n"
    "}\n"
    "\n"
    "function prepareExecution() {\n"
    "  var executable = ShellResolver.getShellConfiguration();\n"
    "  return resolveExecutable(executable);\n"
    "}\n"
    "\n"
    "function resolveExecutable(executable) {\n"
    "  var searchPaths = process.env.PATH.split(':');\n"
    "  return searchPaths[0] + '/' + executable;\n"
    "}\n"
)

_NO_CONTRACT_JS = "function noop() { return 1; }\n"

# The literal historical counterexample from the scoped fix-round-1 review:
# a single generically-named function's Linux branch genuinely returns the
# absolute '/bin/bash', while its own non-Linux branch assigns bare 'bash'
# and passes it to a PATH-searching callee. Fix round 1's function-scoped
# (rather than branch-scoped or named-unit) heuristic wrongly proved this
# bundle PATH-resolved. It uses generic names (not getShellConfiguration/
# prepareExecution), so under the round-2 named-chain validator it is
# rejected simply because the required named units are absent -- this test
# exists to pin the exact documented historical defect, independent of the
# newer named-chain fixtures below.
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

# Link 1a broken: getShellConfiguration's Linux branch selects a shell other
# than bare 'bash', so it never proves "Linux chooses bash" even though the
# value is still fully wired through prepareExecution/resolveExecutable.
_SHELL_CONTRACT_WRONG_SHELL_JS = (
    "function getShellConfiguration() {\n"
    "  if (platform() === 'linux') {\n"
    "    var shellCommand = 'zsh';\n"
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
    "  var searchPaths = process.env.PATH.split(':');\n"
    "  return searchPaths[0] + '/' + executable;\n"
    "}\n"
)

# Link 1b broken: the exact historical counterexample. getShellConfiguration's
# Linux branch genuinely returns the absolute '/bin/bash', while a different,
# non-Linux branch of the very same function assigns a bare 'bash' literal
# that is (correctly, otherwise) wired through prepareExecution and
# resolveExecutable's PATH search. A function-scoped (rather than
# branch-scoped) heuristic would wrongly accept this; the validator must
# reject it because the Linux branch itself never selects bare bash.
_SHELL_CONTRACT_LINUX_BRANCH_ABSOLUTE_JS = (
    "function getShellConfiguration() {\n"
    "  if (platform() === 'linux') {\n"
    "    return '/bin/bash';\n"
    "  }\n"
    "  var shellCommand = 'bash';\n"
    "  return shellCommand;\n"
    "}\n"
    "\n"
    "function prepareExecution() {\n"
    "  var executable = getShellConfiguration();\n"
    "  return resolveExecutable(executable);\n"
    "}\n"
    "\n"
    "function resolveExecutable(executable) {\n"
    "  var searchPaths = process.env.PATH.split(':');\n"
    "  return searchPaths[0] + '/' + executable;\n"
    "}\n"
)

# Link 2a broken: prepareExecution never obtains its value from
# getShellConfiguration at all -- it passes a hardcoded literal straight to
# resolveExecutable, so nothing proves the PATH-resolved value actually came
# from the Linux shell-selection unit.
_SHELL_CONTRACT_NOT_OBTAINED_JS = (
    "function getShellConfiguration() {\n"
    "  if (platform() === 'linux') {\n"
    "    var shellCommand = 'bash';\n"
    "    return shellCommand;\n"
    "  }\n"
    "  return 'cmd.exe';\n"
    "}\n"
    "\n"
    "function prepareExecution() {\n"
    "  return resolveExecutable('bash');\n"
    "}\n"
    "\n"
    "function resolveExecutable(executable) {\n"
    "  var searchPaths = process.env.PATH.split(':');\n"
    "  return searchPaths[0] + '/' + executable;\n"
    "}\n"
)

# Link 2b broken: prepareExecution does call getShellConfiguration, but the
# value it forwards to resolveExecutable is a different, unrelated literal --
# the obtained executable is never actually the one passed along.
_SHELL_CONTRACT_NOT_FORWARDED_JS = (
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
    "  return resolveExecutable('bash');\n"
    "}\n"
    "\n"
    "function resolveExecutable(executable) {\n"
    "  var searchPaths = process.env.PATH.split(':');\n"
    "  return searchPaths[0] + '/' + executable;\n"
    "}\n"
)

# Link 3 broken: bare 'bash' is obtained and forwarded correctly, but
# resolveExecutable resolves it against a hardcoded path list instead of
# process.env.PATH.
_SHELL_CONTRACT_NO_PATH_SEARCH_JS = (
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
    "  var knownPaths = ['/usr/bin', '/bin'];\n"
    "  return knownPaths[0] + '/' + executable;\n"
    "}\n"
)

# Adversarial: the generic tokens ('linux', bare 'bash', process.env.PATH)
# all appear in the file, and even a function literally named
# resolveExecutable performs a PATH search, but no getShellConfiguration or
# prepareExecution unit exists at all -- the named chain cannot be proven
# from token soup alone.
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
    "shell_contract_js",
    [
        pytest.param(_SHELL_CONTRACT_WRONG_SHELL_JS, id="link1a-wrong-shell-selected"),
        pytest.param(
            _SHELL_CONTRACT_LINUX_BRANCH_ABSOLUTE_JS,
            id="link1b-linux-branch-returns-absolute-bin-bash",
        ),
        pytest.param(
            _SHELL_CONTRACT_NOT_OBTAINED_JS,
            id="link2a-executable-not-obtained-from-get-shell-configuration",
        ),
        pytest.param(
            _SHELL_CONTRACT_NOT_FORWARDED_JS,
            id="link2b-obtained-executable-not-forwarded-to-resolve-executable",
        ),
        pytest.param(_SHELL_CONTRACT_NO_PATH_SEARCH_JS, id="link3-resolve-executable-skips-path"),
        pytest.param(
            _SHELL_CONTRACT_UNRELATED_COOCCURRENCE_JS,
            id="adversarial-token-soup-without-named-chain",
        ),
    ],
)
def test_shell_interception_contract_rejects_broken_causal_link(
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


def test_shell_interception_contract_accepts_static_class_method_syntax(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"
    prefix = cache / "agents" / "gemini" / "0.58.0"
    install_fake_package(
        prefix, version="0.58.0", shell_contract_js=_SHELL_CONTRACT_STATIC_METHOD_JS
    )
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
