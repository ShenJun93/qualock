from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import qualock.release_monitor.commands as monitor_commands
from qualock.baseline.io import BaselineStaleError
from qualock.baseline.models import AgentPin, BaselineLock, ModelPin
from qualock.commands import CommandError
from qualock.qualification.models import QualificationResult, Verdict
from qualock.release_monitor.commands import execute_monitor
from qualock.release_monitor.models import MonitorAction, MonitorState, TerminalVerdict
from qualock.release_monitor.state import baseline_sha256

FRESH_SHA = "f" * 64


class FakeReleaseSource:
    def __init__(self, latest: str) -> None:
        self.latest = latest
        self.calls = 0

    def latest_version(self) -> str:
        self.calls += 1
        return self.latest


class FailIfCalledReleaseSource:
    def latest_version(self) -> str:
        raise AssertionError("release discovery must not be called")


class MemoryStateStore:
    def __init__(self, state: MonitorState | None = None, warning: str | None = None) -> None:
        self.state = state
        self.warning = warning
        self.loads = 0
        self.saved: list[MonitorState] = []
        self.save_error: Exception | None = None

    def load(self, root: Path) -> tuple[MonitorState | None, str | None]:
        self.loads += 1
        return self.state, self.warning

    def save(self, root: Path, state: MonitorState) -> None:
        if self.save_error is not None:
            raise self.save_error
        self.saved.append(state)


class FailIfCalledStateStore(MemoryStateStore):
    def load(self, root: Path) -> tuple[MonitorState | None, str | None]:
        raise AssertionError("monitor state must not be read")


def baseline_lock(agent: str = "codex") -> BaselineLock:
    return BaselineLock(
        schema_version=1,
        created_at="2026-09-02T00:00:00+00:00",
        agent=AgentPin(name=agent, version="0.151.0", binary_sha256="a" * 64),
        model=ModelPin(id="gpt-5", snapshot=None, reasoning_effort="medium"),
        qualock_version="0.1.1",
        suite_sha256="b" * 64,
        config_sha256="c" * 64,
        canaries={},
    )


def qualification(verdict: Verdict, candidate: str = "0.152.0") -> QualificationResult:
    return QualificationResult(
        qualification_id="check-test",
        baseline_version="0.151.0",
        candidate_version=candidate,
        verdict=verdict,
        executions=(),
        reasons=(),
        run_order=(),
    )


def patch_project_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("qualock.release_monitor.commands.load_project", lambda root: (object(), []))
    monkeypatch.setattr("qualock.release_monitor.commands.suite_fingerprint", lambda canaries: "suite-now")
    monkeypatch.setattr("qualock.release_monitor.commands.config_fingerprint", lambda config: "config-now")


def test_stale_baseline_stops_before_release_or_state_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch)
    monkeypatch.setattr("qualock.release_monitor.commands.read_baseline_lock", lambda path: baseline_lock())
    monkeypatch.setattr(
        "qualock.release_monitor.commands.assert_suite_fresh",
        lambda *args: (_ for _ in ()).throw(BaselineStaleError("suite changed")),
    )

    with pytest.raises(BaselineStaleError, match="suite changed"):
        execute_monitor(
            tmp_path,
            release_source=FailIfCalledReleaseSource(),
            state_store=FailIfCalledStateStore(),
        )


def test_non_codex_baseline_stops_before_release_or_state_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch)
    monkeypatch.setattr("qualock.release_monitor.commands.read_baseline_lock", lambda path: baseline_lock("other"))
    monkeypatch.setattr("qualock.release_monitor.commands.assert_suite_fresh", lambda *args: None)

    with pytest.raises(CommandError) as exc_info:
        execute_monitor(
            tmp_path,
            release_source=FailIfCalledReleaseSource(),
            state_store=FailIfCalledStateStore(),
        )
    assert str(exc_info.value) == "release monitor supports only a Codex baseline"


def test_missing_baseline_stops_before_release_or_state_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch)

    with pytest.raises(FileNotFoundError):
        execute_monitor(
            tmp_path,
            release_source=FailIfCalledReleaseSource(),
            state_store=FailIfCalledStateStore(),
        )


def test_malformed_baseline_stops_before_release_or_state_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch)
    project = tmp_path / ".qualock"
    project.mkdir()
    (project / "baseline.lock").write_text("{}", encoding="utf-8")

    with pytest.raises(ValidationError):
        execute_monitor(
            tmp_path,
            release_source=FailIfCalledReleaseSource(),
            state_store=FailIfCalledStateStore(),
        )


def patch_fresh_context(
    monkeypatch: pytest.MonkeyPatch, baseline_version: str = "0.151.0"
) -> None:
    monkeypatch.setattr(
        monitor_commands,
        "monitor_preflight",
        lambda root: SimpleNamespace(
            baseline_version=baseline_version,
            baseline_sha256=FRESH_SHA,
        ),
    )


def test_monitor_preflight_reuses_exact_freshness_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    lock = baseline_lock()
    monkeypatch.setattr(
        monitor_commands,
        "load_project",
        lambda root: events.append("load_project") or (object(), []),
    )
    monkeypatch.setattr(
        monitor_commands,
        "read_baseline_lock",
        lambda path: events.append("read_baseline_lock") or lock,
    )
    monkeypatch.setattr(
        monitor_commands,
        "suite_fingerprint",
        lambda canaries: events.append("suite_fingerprint") or "suite-now",
    )
    monkeypatch.setattr(
        monitor_commands,
        "config_fingerprint",
        lambda config: events.append("config_fingerprint") or "config-now",
    )
    monkeypatch.setattr(
        monitor_commands,
        "assert_suite_fresh",
        lambda *args: events.append("assert_suite_fresh"),
    )

    context = monitor_commands.monitor_preflight(tmp_path)

    assert events == [
        "load_project",
        "read_baseline_lock",
        "suite_fingerprint",
        "config_fingerprint",
        "assert_suite_fresh",
    ]
    assert context.baseline_version == lock.agent.version
    assert context.baseline_sha256 == baseline_sha256(lock)


def terminal_state(
    candidate: str = "0.152.0",
    verdict: TerminalVerdict = TerminalVerdict.PASS,
    baseline_sha: str = FRESH_SHA,
) -> MonitorState:
    return MonitorState(
        baseline_sha256=baseline_sha,
        candidate_version=candidate,
        verdict=verdict,
        qualification_id="check-old",
        completed_at="2026-09-02T01:00:00+00:00",
    )


def fail_check(root: Path, candidate_spec: str) -> QualificationResult:
    raise AssertionError(f"check must not run: {candidate_spec}")


@pytest.mark.parametrize("latest", ["0.151.0", "0.150.0"])
def test_same_or_older_than_baseline_does_not_read_state_or_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    latest: str,
) -> None:
    patch_fresh_context(monkeypatch)
    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource(latest),
        state_store=FailIfCalledStateStore(),
        check_executor=fail_check,
    )
    assert outcome.action is MonitorAction.NO_NEW_RELEASE
    assert outcome.latest_version == latest


def test_new_release_is_frozen_to_exact_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch)
    calls: list[str] = []

    def check(root: Path, candidate_spec: str) -> QualificationResult:
        calls.append(candidate_spec)
        return qualification(Verdict.PASS)

    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=MemoryStateStore(),
        check_executor=check,
    )

    assert calls == ["codex@0.152.0"]
    assert outcome.action is MonitorAction.CHECKED


@pytest.mark.parametrize(
    ("terminal", "expected"),
    [
        (TerminalVerdict.PASS, Verdict.PASS),
        (TerminalVerdict.WARN, Verdict.WARN),
        (TerminalVerdict.BLOCK, Verdict.BLOCK),
    ],
)
def test_matching_exact_terminal_state_dedupes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: TerminalVerdict,
    expected: Verdict,
) -> None:
    patch_fresh_context(monkeypatch)
    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=MemoryStateStore(terminal_state(verdict=terminal)),
        check_executor=fail_check,
    )
    assert outcome.action is MonitorAction.ALREADY_QUALIFIED
    assert outcome.recorded_verdict is expected


def test_recorded_newer_candidate_prevents_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch)
    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=MemoryStateStore(terminal_state(candidate="0.153.0")),
        check_executor=fail_check,
    )
    assert outcome.action is MonitorAction.NO_DOWNGRADE


def test_force_reruns_only_exact_matching_newer_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch)
    calls: list[str] = []
    outcome = execute_monitor(
        tmp_path,
        force=True,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=MemoryStateStore(terminal_state()),
        check_executor=lambda root, spec: calls.append(spec)
        or qualification(Verdict.PASS),
    )
    assert calls == ["codex@0.152.0"]
    assert outcome.action is MonitorAction.CHECKED


def test_newer_than_recorded_candidate_qualifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch)
    calls: list[str] = []
    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.153.0"),
        state_store=MemoryStateStore(terminal_state()),
        check_executor=lambda root, spec: calls.append(spec)
        or qualification(Verdict.PASS, "0.153.0"),
    )
    assert calls == ["codex@0.153.0"]
    assert outcome.action is MonitorAction.CHECKED


def test_baseline_sha_mismatch_ignores_record_and_qualifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch)
    calls: list[str] = []
    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=MemoryStateStore(terminal_state(baseline_sha="e" * 64)),
        check_executor=lambda root, spec: calls.append(spec)
        or qualification(Verdict.PASS),
    )
    assert calls == ["codex@0.152.0"]
    assert outcome.action is MonitorAction.CHECKED


def test_force_does_not_bypass_same_or_older_baseline_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch)
    outcome = execute_monitor(
        tmp_path,
        force=True,
        release_source=FakeReleaseSource("0.151.0"),
        state_store=FailIfCalledStateStore(),
        check_executor=fail_check,
    )
    assert outcome.action is MonitorAction.NO_NEW_RELEASE


def test_semantically_equal_differently_spelled_candidate_does_not_dedupe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch)
    calls: list[str] = []
    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=MemoryStateStore(terminal_state(candidate="0.152.0.0")),
        check_executor=lambda root, spec: calls.append(spec)
        or qualification(Verdict.PASS),
    )
    assert calls == ["codex@0.152.0"]
    assert outcome.action is MonitorAction.CHECKED


@pytest.mark.parametrize(
    ("verdict", "terminal"),
    [
        (Verdict.PASS, TerminalVerdict.PASS),
        (Verdict.WARN, TerminalVerdict.WARN),
        (Verdict.BLOCK, TerminalVerdict.BLOCK),
    ],
)
def test_terminal_result_is_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verdict: Verdict,
    terminal: TerminalVerdict,
) -> None:
    patch_fresh_context(monkeypatch)
    store = MemoryStateStore()
    result = qualification(verdict)

    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=store,
        check_executor=lambda root, spec: result,
    )

    assert outcome.qualification_result is result
    assert outcome.state_persisted is True
    assert len(store.saved) == 1
    saved = store.saved[0]
    assert saved.baseline_sha256 == FRESH_SHA
    assert saved.candidate_version == "0.152.0"
    assert saved.verdict is terminal
    assert saved.qualification_id == "check-test"
    assert saved.completed_at


def test_incomplete_is_not_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch)
    store = MemoryStateStore()
    result = qualification(Verdict.INCOMPLETE)

    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=store,
        check_executor=lambda root, spec: result,
    )

    assert outcome.qualification_result is result
    assert outcome.state_persisted is None
    assert store.saved == []


def test_save_failure_preserves_real_verdict_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch)
    store = MemoryStateStore()
    store.save_error = OSError("disk full")
    result = qualification(Verdict.BLOCK)

    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=store,
        check_executor=lambda root, spec: result,
    )

    assert outcome.qualification_result is result
    assert outcome.qualification_result.verdict is Verdict.BLOCK
    assert outcome.state_persisted is False
    assert outcome.state_warning is not None
    assert "disk full" in outcome.state_warning


def test_load_warning_does_not_block_real_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch)
    store = MemoryStateStore(warning="release monitor state ignored: [literal]")

    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=store,
        check_executor=lambda root, spec: qualification(Verdict.PASS),
    )

    assert outcome.action is MonitorAction.CHECKED
    assert outcome.state_warning == "release monitor state ignored: [literal]"


def test_load_and_save_warnings_are_combined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_fresh_context(monkeypatch)
    store = MemoryStateStore(warning="state was corrupt")
    store.save_error = OSError("disk full")

    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=store,
        check_executor=lambda root, spec: qualification(Verdict.WARN),
    )

    assert outcome.state_warning is not None
    assert "state was corrupt" in outcome.state_warning
    assert "disk full" in outcome.state_warning
