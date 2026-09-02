import hashlib
import json
import os
from pathlib import Path

import pytest

from qualock.baseline.models import AgentPin, BaselineLock, ModelPin
from qualock.evidence.fingerprint import sha256_canonical
from qualock.release_monitor.models import MonitorState, TerminalVerdict
from qualock.release_monitor.state import FileMonitorStateStore, baseline_sha256, project_key


def sample_baseline_lock() -> BaselineLock:
    return BaselineLock(
        schema_version=1,
        created_at="2026-09-02T00:00:00+00:00",
        agent=AgentPin(name="codex", version="0.151.0", binary_sha256="a" * 64),
        model=ModelPin(id="gpt-5", snapshot=None, reasoning_effort="medium"),
        qualock_version="0.1.1",
        suite_sha256="b" * 64,
        config_sha256="c" * 64,
        canaries={},
    )


def sample_state(verdict: TerminalVerdict = TerminalVerdict.PASS) -> MonitorState:
    return MonitorState(
        baseline_sha256="d" * 64,
        candidate_version="0.152.0",
        verdict=verdict,
        qualification_id="check-test",
        completed_at="2026-09-02T01:00:00+00:00",
    )


def test_project_key_uses_normalized_absolute_root(tmp_path: Path) -> None:
    expected = hashlib.sha256(
        os.path.normcase(str(tmp_path.resolve())).encode("utf-8")
    ).hexdigest()
    assert project_key(tmp_path) == expected


def test_default_state_path_is_outside_project(tmp_path: Path, monkeypatch) -> None:
    user_state = tmp_path / "user-state"
    monkeypatch.setattr("qualock.release_monitor.state.user_state_dir", lambda app: str(user_state))
    store = FileMonitorStateStore()
    project = tmp_path / "project"

    path = store.path_for(project)

    assert path.parent == user_state / "release-monitor/projects"
    assert path.name == f"{project_key(project)}.json"
    assert project not in path.parents


def test_baseline_sha_is_canonical_for_parsed_lock() -> None:
    lock = sample_baseline_lock()
    expected = sha256_canonical(lock.model_dump(mode="json"))
    assert baseline_sha256(lock) == expected


@pytest.mark.parametrize("verdict", list(TerminalVerdict))
def test_terminal_state_round_trips_atomically(tmp_path: Path, verdict: TerminalVerdict) -> None:
    store = FileMonitorStateStore(base_dir=tmp_path / "state")
    state = sample_state(verdict)

    store.save(tmp_path / "project", state)
    loaded, warning = store.load(tmp_path / "project")

    assert loaded == state
    assert warning is None
    path = store.path_for(tmp_path / "project")
    assert json.loads(path.read_text(encoding="utf-8"))["verdict"] == verdict.value
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []


def test_missing_state_is_clean_absence(tmp_path: Path) -> None:
    store = FileMonitorStateStore(base_dir=tmp_path / "state")
    assert store.load(tmp_path / "project") == (None, None)


@pytest.mark.parametrize(
    "payload",
    [
        "{not-json",
        '{"schema_version": 99}',
        (
            '{"schema_version": 1, "baseline_sha256": "x", "agent": "codex", '
            '"candidate_version": "not-a-version", "verdict": "pass", '
            '"qualification_id": "q", "completed_at": "now"}'
        ),
        (
            '{"schema_version": 1, "baseline_sha256": "x", "agent": "codex", '
            '"candidate_version": "0.152.0", "verdict": "incomplete", '
            '"qualification_id": "q", "completed_at": "now"}'
        ),
    ],
)
def test_invalid_state_is_ignored_with_warning(tmp_path: Path, payload: str) -> None:
    store = FileMonitorStateStore(base_dir=tmp_path / "state")
    path = store.path_for(tmp_path / "project")
    path.parent.mkdir(parents=True)
    path.write_text(payload, encoding="utf-8")

    loaded, warning = store.load(tmp_path / "project")

    assert loaded is None
    assert warning is not None
    assert "ignored" in warning
