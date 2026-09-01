import subprocess
import sys
from pathlib import Path

import pytest

from qualock.config.models import ProjectProtectionConfig
from qualock.project_protection.io import read_project_lock, write_project_lock
from qualock.project_protection.models import ProtectionStatus
from qualock.project_protection.runner import (
    ProtectionBaselineError,
    create_project_lock,
    run_protections,
)


def protection(command: list[str], *, timeout: int = 5) -> ProjectProtectionConfig:
    return ProjectProtectionConfig(
        id="smoke",
        name="Project smoke check",
        command=command,
        timeout_seconds=timeout,
    )


def init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    (root / "README.md").write_text("ok\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)


def test_run_protections_classifies_pass_fail_and_incomplete(tmp_path: Path) -> None:
    definitions = [
        ProjectProtectionConfig(id="pass", name="Pass", command=[sys.executable, "-c", "print('ok')"], timeout_seconds=5),
        ProjectProtectionConfig(id="fail", name="Fail", command=[sys.executable, "-c", "raise SystemExit(7)"], timeout_seconds=5),
        ProjectProtectionConfig(id="missing", name="Missing", command=["qualock-command-that-does-not-exist"], timeout_seconds=5),
    ]

    runs = run_protections(tmp_path, definitions)

    assert [run.status for run in runs] == [ProtectionStatus.PASS, ProtectionStatus.FAIL, ProtectionStatus.INCOMPLETE]
    assert runs[0].stdout.strip() == "ok"
    assert runs[1].exit_code == 7
    assert runs[2].exit_code is None
    assert runs[2].error


def test_run_protections_marks_timeout_incomplete(tmp_path: Path) -> None:
    runs = run_protections(
        tmp_path,
        [protection([sys.executable, "-c", "import time; time.sleep(2)"], timeout=1)],
    )

    assert runs[0].status is ProtectionStatus.INCOMPLETE
    assert runs[0].timed_out is True


def test_create_project_lock_requires_all_checks_to_pass(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    definitions = [protection([sys.executable, "-c", "raise SystemExit(1)"])]
    runs = run_protections(tmp_path, definitions)

    with pytest.raises(ProtectionBaselineError):
        create_project_lock(tmp_path, definitions, runs, created_at="2026-09-01T00:00:00Z")


def test_project_lock_round_trip_records_git_and_definitions(tmp_path: Path) -> None:
    init_git_repo(tmp_path)
    definitions = [protection([sys.executable, "-c", "print('healthy')"])]
    runs = run_protections(tmp_path, definitions)
    lock = create_project_lock(tmp_path, definitions, runs, created_at="2026-09-01T00:00:00Z")
    path = tmp_path / ".qualock" / "project.lock"

    write_project_lock(path, lock)
    loaded = read_project_lock(path)

    assert loaded.created_at == "2026-09-01T00:00:00Z"
    assert len(loaded.git_head) == 40
    assert loaded.git_dirty is False
    assert loaded.protections[0].name == "Project smoke check"
    assert loaded.baseline[0].status is ProtectionStatus.PASS
