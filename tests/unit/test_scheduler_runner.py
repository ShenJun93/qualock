from datetime import UTC, datetime
from pathlib import Path

import pytest

from qualock.release_monitor.state import project_key
from qualock.run.process import ProcessResult
from qualock.scheduler.models import SchedulerBackendKind, ScheduleRegistration, native_id_for
from qualock.scheduler.runner import main, run_registered_monitor
from qualock.scheduler.state import (
    FileRegistrationStore,
    RegistrationLoad,
    RegistrationLoadKind,
)


def saved_registration(
    base_dir: Path,
    *,
    project_root: Path,
    python_executable: Path,
    path_env: str,
) -> tuple[FileRegistrationStore, ScheduleRegistration]:
    key = project_key(project_root)
    registration = ScheduleRegistration(
        project_key=key,
        project_root=project_root,
        backend=SchedulerBackendKind.SYSTEMD_USER,
        native_id=native_id_for(SchedulerBackendKind.SYSTEMD_USER, key),
        hour=9,
        minute=0,
        python_executable=python_executable,
        runner_working_directory=base_dir.resolve(),
        path_env=path_env,
        enabled_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    store = FileRegistrationStore(base_dir)
    store.save(registration)
    return store, registration


def test_runner_invokes_only_monitor_and_propagates_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    store, registration = saved_registration(
        tmp_path / "state",
        project_root=project.resolve(),
        python_executable=python.resolve(),
        path_env="/registered/bin",
    )
    calls: list[tuple[list[str], Path | None, dict[str, str]]] = []
    monkeypatch.setattr("qualock.scheduler.runner.sys.executable", str(python.resolve()))

    def fake_run(argv, *, cwd, env, timeout_seconds):
        calls.append((list(argv), cwd, dict(env)))
        return ProcessResult(2, "blocked\n", "detail\n", 0.1, False)

    result = run_registered_monitor(
        registration.project_key,
        store=store,
        process_runner=fake_run,
        environ={"PATH": "/scheduler/bin", "KEEP": "yes"},
    )

    assert result == 2
    assert calls == [
        (
            [str(python.resolve()), "-m", "qualock.cli", "monitor"],
            project.resolve(),
            {"PATH": "/registered/bin", "KEEP": "yes"},
        )
    ]
    log = store.log_path(registration.project_key).read_text(encoding="utf-8")
    assert "START project=" in log
    assert "blocked\ndetail\n" in log
    assert "EXIT code=2" in log


@pytest.mark.parametrize("key", ["", "A" * 64, "x" * 64, "0" * 63])
def test_invalid_key_never_runs(tmp_path: Path, key: str) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("process runner must not be called")

    assert run_registered_monitor(key, store=FileRegistrationStore(tmp_path), process_runner=fail) == 1


@pytest.mark.parametrize("corrupt", [False, True])
def test_unavailable_registration_never_runs(tmp_path: Path, corrupt: bool) -> None:
    store = FileRegistrationStore(tmp_path)
    key = "a" * 64
    if corrupt:
        path = store.registration_path(key)
        path.parent.mkdir(parents=True)
        path.write_text("{}", encoding="utf-8")

    def fail(*args, **kwargs):
        raise AssertionError("process runner must not be called")

    assert run_registered_monitor(key, store=store, process_runner=fail) == 1


def test_missing_root_never_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    store, registration = saved_registration(
        tmp_path / "state", project_root=project.resolve(), python_executable=python.resolve(), path_env=""
    )
    project.rmdir()
    monkeypatch.setattr("qualock.scheduler.runner.sys.executable", str(python.resolve()))

    def fail(*args, **kwargs):
        raise AssertionError("process runner must not be called")

    assert run_registered_monitor(registration.project_key, store=store, process_runner=fail) == 1


def test_python_mismatch_never_runs(tmp_path: Path) -> None:
    registered = tmp_path / "registered-python"
    registered.write_text("", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    store, registration = saved_registration(
        tmp_path / "state", project_root=project.resolve(), python_executable=registered.resolve(), path_env=""
    )

    def fail(*args, **kwargs):
        raise AssertionError("process runner must not be called")

    assert run_registered_monitor(registration.project_key, store=store, process_runner=fail) == 1


@pytest.mark.parametrize("mismatch", ["key", "root"])
def test_registration_identity_mismatch_never_runs(
    tmp_path: Path, mismatch: str
) -> None:
    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    store, registration = saved_registration(
        tmp_path / "state",
        project_root=project.resolve(),
        python_executable=python.resolve(),
        path_env="",
    )
    if mismatch == "key":
        forged = registration.model_copy(update={"project_key": "b" * 64})
    else:
        other_root = tmp_path / "other"
        other_root.mkdir()
        forged = registration.model_copy(update={"project_root": other_root.resolve()})
    store.load = lambda key: RegistrationLoad(RegistrationLoadKind.VALID, forged)  # type: ignore[method-assign]

    def fail(*args, **kwargs):
        raise AssertionError("process runner must not be called")

    assert run_registered_monitor(
        registration.project_key, store=store, process_runner=fail
    ) == 1


@pytest.mark.parametrize("result", [
    ProcessResult(None, "partial", "", 1.0, True),
    ProcessResult(None, "", "missing", 0.1, False),
])
def test_no_exit_code_fails_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: ProcessResult) -> None:
    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    store, registration = saved_registration(
        tmp_path / "state", project_root=project.resolve(), python_executable=python.resolve(), path_env=""
    )
    monkeypatch.setattr("qualock.scheduler.runner.sys.executable", str(python.resolve()))
    calls = 0

    def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return result

    assert run_registered_monitor(registration.project_key, store=store, process_runner=run) == 1
    assert calls == 1


def test_logging_failure_does_not_change_child_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    store, registration = saved_registration(
        tmp_path / "state", project_root=project.resolve(), python_executable=python.resolve(), path_env=""
    )
    monkeypatch.setattr("qualock.scheduler.runner.sys.executable", str(python.resolve()))
    real_open = Path.open

    def fail_log_open(path: Path, *args, **kwargs):
        if path == store.log_path(registration.project_key):
            raise OSError("no log")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_log_open)
    assert run_registered_monitor(
        registration.project_key,
        store=store,
        process_runner=lambda *args, **kwargs: ProcessResult(2, "", "", 0.1, False),
    ) == 2


def test_main_rejects_arbitrary_command() -> None:
    with pytest.raises(SystemExit, match="2"):
        main(["--project-key", "a" * 64, "--command", "anything"])
