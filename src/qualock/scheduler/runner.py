from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from qualock.release_monitor.state import project_key as project_key_for

from .models import ProcessRunner, run_process
from .state import FileRegistrationStore, RegistrationLoadKind, RegistrationStore

MONITOR_TIMEOUT_SECONDS = 86400.0
_PROJECT_KEY = re.compile(r"^[0-9a-f]{64}$")


def _utc_now(now: Callable[[], datetime] | None) -> str:
    value = now() if now is not None else datetime.now(UTC)
    return value.astimezone(UTC).isoformat()


def _append_log(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            path.parent.chmod(0o700)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)
        if os.name != "nt":
            path.chmod(0o600)
    except OSError:
        return


def _append_runner_error(
    path: Path,
    message: str,
    now: Callable[[], datetime] | None,
) -> None:
    _append_log(path, f"[{_utc_now(now)}] ERROR {message}\n")


def run_registered_monitor(
    project_key: str,
    *,
    store: RegistrationStore | None = None,
    process_runner: ProcessRunner = run_process,
    environ: Mapping[str, str] | None = None,
    now: Callable[[], datetime] | None = None,
) -> int:
    if _PROJECT_KEY.fullmatch(project_key) is None:
        return 1
    active_store = store or FileRegistrationStore()
    loaded = active_store.load(project_key)
    log_path = active_store.log_path(project_key)
    if loaded.kind is not RegistrationLoadKind.VALID or loaded.registration is None:
        _append_runner_error(log_path, "registration unavailable", now)
        return 1

    registration = loaded.registration
    try:
        if registration.project_key != project_key:
            raise ValueError("registration project key does not match runner key")
        if project_key_for(registration.project_root) != project_key:
            raise ValueError("registration project root does not match runner key")
        if not registration.project_root.is_dir():
            raise ValueError("registered project root is unavailable")
        if not registration.python_executable.samefile(Path(sys.executable)):
            raise ValueError("registered Python does not match current Python")
    except (OSError, ValueError) as exc:
        _append_runner_error(log_path, str(exc), now)
        return 1

    child_env = dict(os.environ if environ is None else environ)
    child_env["PATH"] = registration.path_env
    argv = [sys.executable, "-m", "qualock.cli", "monitor"]
    _append_log(log_path, f"[{_utc_now(now)}] START project={registration.project_root}\n")
    try:
        result = process_runner(
            argv,
            cwd=registration.project_root,
            env=child_env,
            timeout_seconds=MONITOR_TIMEOUT_SECONDS,
        )
    except OSError as exc:
        _append_runner_error(log_path, str(exc), now)
        return 1
    _append_log(log_path, result.stdout + result.stderr)
    exit_code = 1 if result.timed_out or result.exit_code is None else result.exit_code
    _append_log(log_path, f"[{_utc_now(now)}] EXIT code={exit_code}\n")
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m qualock.scheduler.runner")
    parser.add_argument("--project-key", required=True)
    args = parser.parse_args(argv)
    return run_registered_monitor(args.project_key)


if __name__ == "__main__":
    raise SystemExit(main())
