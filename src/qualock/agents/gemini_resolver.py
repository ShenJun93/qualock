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


# Shell-interception source-contract proof: rather than checking whether
# three tokens ('linux', bare 'bash', process.env.PATH) merely co-occur
# anywhere in a bundle file, this anchors each signal to a named function's
# bounded brace scope and follows the actual call graph, so the check only
# passes when the value chosen on Linux is bare 'bash' (not an absolute
# path), that value is passed into a call to another function defined in the
# same file, and that callee's body itself references process.env.PATH.
_FUNCTION_DEF_RE = re.compile(r"function\s+(\w+)\s*\([^)]*\)\s*\{")
_LINUX_SELECT_RE = re.compile(r"platform\(\)\s*===\s*['\"]linux['\"]")
_BASH_ASSIGN_RE = re.compile(r"(?:var|let|const)\s+(\w+)\s*=\s*['\"]bash['\"]")
_BASH_LITERAL_RE = re.compile(r"['\"]bash['\"]")
_PATH_LOOKUP_RE = re.compile(r"process\.env\.PATH")
_CALL_EXPR_RE = re.compile(r"(\w+)\s*\(([^()]*)\)")


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


def _extract_named_functions(text: str) -> dict[str, str]:
    functions: dict[str, str] = {}
    for match in _FUNCTION_DEF_RE.finditer(text):
        brace_index = match.end() - 1
        body = _match_brace_scope(text, brace_index)
        if body is not None:
            functions[match.group(1)] = body
    return functions


def _proves_path_resolved_bash_shell(text: str) -> bool:
    functions = _extract_named_functions(text)
    for body in functions.values():
        if _function_selects_path_resolved_bash(body, functions):
            return True
    return False


def _function_selects_path_resolved_bash(body: str, functions: dict[str, str]) -> bool:
    if not _LINUX_SELECT_RE.search(body):
        return False

    bash_vars = set(_BASH_ASSIGN_RE.findall(body))
    has_bare_bash_literal = bool(bash_vars) or bool(_BASH_LITERAL_RE.search(body))
    if not has_bare_bash_literal:
        return False

    # The selected bash value must actually be handed to another named
    # function (the upstream executable resolver) whose own body performs
    # the PATH search — mere co-occurrence within this function's scope is
    # not proof that the value is PATH-resolved rather than used verbatim.
    for call_match in _CALL_EXPR_RE.finditer(body):
        callee, args = call_match.group(1), call_match.group(2)
        callee_body = functions.get(callee)
        if callee_body is None:
            continue
        references_bash_value = any(var in args for var in bash_vars) or bool(
            _BASH_LITERAL_RE.search(args)
        )
        if references_bash_value and _PATH_LOOKUP_RE.search(callee_body):
            return True

    return False


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
            if _proves_path_resolved_bash_shell(text):
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
