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

_SHELL_CONTRACT_JS = (
    "function pickShell() {\n"
    "  if (platform() === 'linux') {\n"
    "    var shellCommand = 'bash';\n"
    "    return resolveExecutable(shellCommand);\n"
    "  }\n"
    "  return null;\n"
    "}\n"
    "\n"
    "function resolveExecutable(shellCommand) {\n"
    "  var searchPaths = process.env.PATH.split(':');\n"
    "  return searchPaths[0] + '/' + shellCommand;\n"
    "}\n"
)

_NO_CONTRACT_JS = "function noop() { return 1; }\n"

# Link 1 broken: the Linux branch selects a shell other than bare 'bash', so
# no function proves "Linux chooses bash" even though the value is still
# PATH-resolved.
_SHELL_CONTRACT_WRONG_SHELL_JS = (
    "function pickShell() {\n"
    "  if (platform() === 'linux') {\n"
    "    var shellCommand = 'zsh';\n"
    "    return resolveExecutable(shellCommand);\n"
    "  }\n"
    "  return null;\n"
    "}\n"
    "\n"
    "function resolveExecutable(shellCommand) {\n"
    "  var searchPaths = process.env.PATH.split(':');\n"
    "  return searchPaths[0] + '/' + shellCommand;\n"
    "}\n"
)

# Link 2 broken: bare 'bash' is selected on Linux, but that value is never
# passed to any executable-resolution call, so nothing proves it is
# PATH-resolved rather than used verbatim.
_SHELL_CONTRACT_NOT_PASSED_JS = (
    "function pickShell() {\n"
    "  if (platform() === 'linux') {\n"
    "    var shellCommand = 'bash';\n"
    "    return shellCommand;\n"
    "  }\n"
    "  return null;\n"
    "}\n"
    "\n"
    "function unrelatedPathLookup() {\n"
    "  return process.env.PATH;\n"
    "}\n"
)

# Link 3 broken: bare 'bash' is selected and passed to a call, but the called
# function resolves it against a hardcoded path list instead of PATH.
_SHELL_CONTRACT_NO_PATH_SEARCH_JS = (
    "function pickShell() {\n"
    "  if (platform() === 'linux') {\n"
    "    var shellCommand = 'bash';\n"
    "    return resolveExecutable(shellCommand);\n"
    "  }\n"
    "  return null;\n"
    "}\n"
    "\n"
    "function resolveExecutable(shellCommand) {\n"
    "  var knownPaths = ['/usr/bin', '/bin'];\n"
    "  return knownPaths[0] + '/' + shellCommand;\n"
    "}\n"
)

# Adversarial: all three raw tokens ('linux', bare 'bash', process.env.PATH)
# co-occur in the file, in unrelated functions, while the actual Linux shell
# selection uses an absolute /bin/bash path. Full-file token co-occurrence
# would wrongly accept this; per-function causal linkage must reject it.
_SHELL_CONTRACT_UNRELATED_COOCCURRENCE_JS = (
    "function pickShell() {\n"
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
    "function findGit() {\n"
    "  var dirs = process.env.PATH.split(':');\n"
    "  return dirs[0] + '/git';\n"
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
        pytest.param(_SHELL_CONTRACT_WRONG_SHELL_JS, id="link1-wrong-shell-selected"),
        pytest.param(_SHELL_CONTRACT_NOT_PASSED_JS, id="link2-bash-not-passed-to-resolver"),
        pytest.param(_SHELL_CONTRACT_NO_PATH_SEARCH_JS, id="link3-resolver-skips-path-search"),
        pytest.param(
            _SHELL_CONTRACT_UNRELATED_COOCCURRENCE_JS,
            id="adversarial-unrelated-cooccurrence-absolute-bin-bash",
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
