from pathlib import Path

import pytest

import qualock.version_bisect.commands as bisect_commands
from qualock.baseline.io import BaselineStaleError
from qualock.baseline.models import AgentPin, BaselineLock, ModelPin
from qualock.commands import CommandError
from qualock.qualification.models import QualificationResult, Verdict
from qualock.version_bisect.commands import BisectPreflight, execute_bisect
from qualock.version_bisect.models import BisectAgent, BisectStep, BisectStop


class FakeCatalog:
    def __init__(self, versions: tuple[str, ...]) -> None:
        self.versions = versions
        self.calls = 0

    def stable_versions(self) -> tuple[str, ...]:
        self.calls += 1
        return self.versions


class FailStore:
    def create(self, *, agent: BisectAgent, **kwargs: object) -> Path:
        raise AssertionError("store must not be used")

    def save(self, *, agent: BisectAgent, **kwargs: object) -> None:
        raise AssertionError("store must not be used")


class MemoryStore:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.saved: list[dict[str, object]] = []

    def create(self, *, agent: BisectAgent, **kwargs: object) -> Path:
        self.created.append({**kwargs, "agent": agent})
        return Path("/memory") / str(kwargs["bisect_id"])

    def save(self, *, agent: BisectAgent, **kwargs: object) -> None:
        self.saved.append({**kwargs, "agent": agent})


def fail_check(root: Path, candidate_spec: str) -> QualificationResult:
    raise AssertionError(f"check must not run: {candidate_spec}")


def qualification(candidate: str, verdict: Verdict, qualification_id: str) -> QualificationResult:
    return QualificationResult(
        qualification_id=qualification_id,
        baseline_version="0.151.0",
        candidate_version=candidate,
        verdict=verdict,
        executions=(),
        reasons=(),
        run_order=(),
    )


def patch_preflight(
    monkeypatch: pytest.MonkeyPatch,
    *,
    agent_name: BisectAgent = "codex",
    baseline: str = "0.151.0",
) -> None:
    monkeypatch.setattr(
        bisect_commands,
        "bisect_preflight",
        lambda root: BisectPreflight(agent_name=agent_name, baseline_version=baseline),
    )


def baseline_lock(agent: str = "codex", version: str = "0.151.0") -> BaselineLock:
    return BaselineLock(
        schema_version=1,
        created_at="2026-09-02T00:00:00+00:00",
        agent=AgentPin(name=agent, version=version, binary_sha256="a" * 64),
        model=ModelPin(id="gpt-5", snapshot=None, reasoning_effort="medium"),
        qualock_version="0.1.1",
        suite_sha256="b" * 64,
        config_sha256="c" * 64,
        canaries={},
    )


class FakeAgentConfig:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeConfig:
    def __init__(self, agent_name: str) -> None:
        self.agent = FakeAgentConfig(agent_name)


def patch_project_loading(monkeypatch: pytest.MonkeyPatch, *, agent_name: str = "codex") -> None:
    monkeypatch.setattr(bisect_commands, "load_project", lambda root: (FakeConfig(agent_name), []))
    monkeypatch.setattr(bisect_commands, "suite_fingerprint", lambda canaries: "suite-now")
    monkeypatch.setattr(bisect_commands, "config_fingerprint", lambda config: "config-now")


# --- Step 1: preflight / range validation ---------------------------------


@pytest.mark.parametrize("upper", ["codex@latest", "codex@0.153.0-beta.1", "other@0.153.0"])
def test_invalid_upper_stops_before_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upper: str
) -> None:
    patch_preflight(monkeypatch, baseline="0.151.0")
    catalog = FakeCatalog(("0.152.0", "0.153.0"))
    with pytest.raises(CommandError):
        execute_bisect(
            tmp_path, upper, catalog=catalog, summary_store=MemoryStore(), check_executor=fail_check
        )
    assert catalog.calls == 0


@pytest.mark.parametrize("upper", ["0.151.0", "0.150.0", "0.154.0"])
def test_upper_must_be_newer_and_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upper: str
) -> None:
    patch_preflight(monkeypatch, baseline="0.151.0")
    with pytest.raises(CommandError):
        execute_bisect(
            tmp_path,
            f"codex@{upper}",
            catalog=FakeCatalog(("0.150.0", "0.151.0", "0.152.0", "0.153.0")),
            summary_store=MemoryStore(),
            check_executor=fail_check,
        )


def test_non_stable_baseline_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_project_loading(monkeypatch)
    monkeypatch.setattr(
        bisect_commands, "read_baseline_lock", lambda path: baseline_lock(version="0.151.0-beta.1")
    )
    monkeypatch.setattr(bisect_commands, "assert_suite_fresh", lambda *args: None)
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(CommandError):
        execute_bisect(
            tmp_path,
            "codex@0.153.0",
            catalog=catalog,
            summary_store=FailStore(),
            check_executor=fail_check,
        )
    assert catalog.calls == 0


def test_config_lock_agent_mismatch_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_project_loading(monkeypatch, agent_name="claude")
    monkeypatch.setattr(bisect_commands, "read_baseline_lock", lambda path: baseline_lock(agent="codex"))
    monkeypatch.setattr(bisect_commands, "assert_suite_fresh", lambda *args: None)
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(CommandError):
        execute_bisect(
            tmp_path,
            "codex@0.153.0",
            catalog=catalog,
            summary_store=FailStore(),
            check_executor=fail_check,
        )
    assert catalog.calls == 0


def test_antigravity_baseline_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_project_loading(monkeypatch, agent_name="antigravity")
    monkeypatch.setattr(
        bisect_commands,
        "read_baseline_lock",
        lambda path: baseline_lock(agent="antigravity", version="1.0.0"),
    )
    monkeypatch.setattr(bisect_commands, "assert_suite_fresh", lambda *args: None)
    catalog = FakeCatalog(("1.0.0", "1.1.0"))

    with pytest.raises(CommandError):
        execute_bisect(
            tmp_path,
            "antigravity@1.1.0",
            catalog=catalog,
            summary_store=FailStore(),
            check_executor=fail_check,
        )
    assert catalog.calls == 0


def test_unknown_agent_baseline_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_project_loading(monkeypatch, agent_name="other")
    monkeypatch.setattr(bisect_commands, "read_baseline_lock", lambda path: baseline_lock(agent="other"))
    monkeypatch.setattr(bisect_commands, "assert_suite_fresh", lambda *args: None)
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(CommandError):
        execute_bisect(
            tmp_path,
            "codex@0.153.0",
            catalog=catalog,
            summary_store=FailStore(),
            check_executor=fail_check,
        )
    assert catalog.calls == 0


def test_bisect_preflight_accepts_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    monkeypatch.setattr(
        bisect_commands, "read_baseline_lock", lambda path: baseline_lock(agent="codex", version="0.151.0")
    )
    monkeypatch.setattr(bisect_commands, "assert_suite_fresh", lambda *args: None)

    result = bisect_commands.bisect_preflight(tmp_path)

    assert result == BisectPreflight(agent_name="codex", baseline_version="0.151.0")


def test_bisect_preflight_accepts_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_project_loading(monkeypatch, agent_name="claude")
    monkeypatch.setattr(
        bisect_commands,
        "read_baseline_lock",
        lambda path: baseline_lock(agent="claude", version="2.1.260"),
    )
    monkeypatch.setattr(bisect_commands, "assert_suite_fresh", lambda *args: None)

    result = bisect_commands.bisect_preflight(tmp_path)

    assert result == BisectPreflight(agent_name="claude", baseline_version="2.1.260")


def test_cross_agent_upper_rejected_before_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_preflight(monkeypatch, agent_name="claude", baseline="2.1.260")
    catalog = FakeCatalog(("2.1.261", "2.1.263"))

    with pytest.raises(CommandError):
        execute_bisect(
            tmp_path,
            "codex@2.1.261",
            catalog=catalog,
            summary_store=FailStore(),
            check_executor=fail_check,
        )
    assert catalog.calls == 0


def test_stale_baseline_stops_before_catalog_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch)
    monkeypatch.setattr(bisect_commands, "read_baseline_lock", lambda path: baseline_lock())
    monkeypatch.setattr(
        bisect_commands,
        "assert_suite_fresh",
        lambda *args: (_ for _ in ()).throw(BaselineStaleError("suite changed")),
    )
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(BaselineStaleError, match="suite changed"):
        execute_bisect(
            tmp_path,
            "codex@0.153.0",
            catalog=catalog,
            summary_store=MemoryStore(),
            check_executor=fail_check,
        )
    assert catalog.calls == 0


# --- Step 2: verdict decisions and provenance ordering ---------------------


def test_pass_prefix_then_block_is_first_bad(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_preflight(monkeypatch, baseline="0.151.0")
    calls: list[str] = []
    results = {
        "codex@0.152.0": qualification("0.152.0", Verdict.PASS, "check-152"),
        "codex@0.153.0": qualification("0.153.0", Verdict.BLOCK, "check-153"),
        "codex@0.154.0": qualification("0.154.0", Verdict.PASS, "check-154"),
    }
    outcome = execute_bisect(
        tmp_path,
        "codex@0.154.0",
        catalog=FakeCatalog(("0.150.0", "0.152.0", "0.153.0", "0.154.0")),
        summary_store=MemoryStore(),
        check_executor=lambda root, spec: calls.append(spec) or results[spec],
        bisect_id="bisect-test",
    )
    assert calls == ["codex@0.152.0", "codex@0.153.0"]
    assert outcome.agent_name == "codex"
    assert outcome.stop_reason is BisectStop.FIRST_BAD_FOUND
    assert outcome.last_known_good == "0.152.0"
    assert outcome.first_bad == "0.153.0"


@pytest.mark.parametrize(
    ("verdict", "stop"),
    [
        (Verdict.WARN, BisectStop.WARN_UNRESOLVED),
        (Verdict.INCOMPLETE, BisectStop.INCOMPLETE),
    ],
)
def test_warn_or_incomplete_stops_after_first_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verdict: Verdict, stop: BisectStop
) -> None:
    patch_preflight(monkeypatch, baseline="0.151.0")
    calls: list[str] = []

    def check(root: Path, spec: str) -> QualificationResult:
        calls.append(spec)
        return qualification(spec.split("@", 1)[1], verdict, "check-1")

    outcome = execute_bisect(
        tmp_path,
        "codex@0.154.0",
        catalog=FakeCatalog(("0.150.0", "0.152.0", "0.153.0", "0.154.0")),
        summary_store=MemoryStore(),
        check_executor=check,
        bisect_id="bisect-test",
    )
    assert calls == ["codex@0.152.0"]
    assert outcome.agent_name == "codex"
    assert outcome.first_bad is None
    assert outcome.stop_reason is stop


def test_all_pass_scans_full_range_excluding_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_preflight(monkeypatch, baseline="0.151.0")
    calls: list[str] = []

    def check(root: Path, spec: str) -> QualificationResult:
        calls.append(spec)
        return qualification(spec.split("@", 1)[1], Verdict.PASS, "check-1")

    outcome = execute_bisect(
        tmp_path,
        "codex@0.154.0",
        catalog=FakeCatalog(("0.150.0", "0.151.0", "0.153.0", "0.152.0", "0.154.0")),
        summary_store=MemoryStore(),
        check_executor=check,
        bisect_id="bisect-test",
    )
    assert calls == ["codex@0.152.0", "codex@0.153.0", "codex@0.154.0"]
    assert outcome.agent_name == "codex"
    assert outcome.stop_reason is BisectStop.NO_BAD_FOUND
    assert outcome.first_bad is None
    assert outcome.last_known_good == "0.154.0"


# --- Step 3: agent-scoped upper bound and candidate execution --------------


def test_claude_all_pass_scans_full_range_excluding_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_preflight(monkeypatch, agent_name="claude", baseline="2.1.260")
    calls: list[str] = []
    store = MemoryStore()

    def check(root: Path, spec: str) -> QualificationResult:
        calls.append(spec)
        return qualification(spec.split("@", 1)[1], Verdict.PASS, "check-1")

    outcome = execute_bisect(
        tmp_path,
        "claude@2.1.263",
        catalog=FakeCatalog(("2.1.260", "2.1.261", "2.1.263")),
        summary_store=store,
        check_executor=check,
        bisect_id="bisect-test",
    )
    assert calls == ["claude@2.1.261", "claude@2.1.263"]
    assert outcome.agent_name == "claude"
    assert outcome.stop_reason is BisectStop.NO_BAD_FOUND
    assert outcome.first_bad is None
    assert outcome.last_known_good == "2.1.263"
    assert store.created[0]["agent"] == "claude"
    assert [call["agent"] for call in store.saved] == ["claude"] * len(store.saved)


def test_claude_pass_then_block_stops_at_first_bad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_preflight(monkeypatch, agent_name="claude", baseline="2.1.260")
    calls: list[str] = []
    results = {
        "claude@2.1.261": qualification("2.1.261", Verdict.PASS, "check-261"),
        "claude@2.1.263": qualification("2.1.263", Verdict.BLOCK, "check-263"),
    }
    store = MemoryStore()

    outcome = execute_bisect(
        tmp_path,
        "claude@2.1.263",
        catalog=FakeCatalog(("2.1.260", "2.1.261", "2.1.263")),
        summary_store=store,
        check_executor=lambda root, spec: calls.append(spec) or results[spec],
        bisect_id="bisect-test",
    )
    assert calls == ["claude@2.1.261", "claude@2.1.263"]
    assert outcome.agent_name == "claude"
    assert outcome.stop_reason is BisectStop.FIRST_BAD_FOUND
    assert outcome.last_known_good == "2.1.261"
    assert outcome.first_bad == "2.1.263"
    assert store.created[0]["agent"] == "claude"
    assert [call["agent"] for call in store.saved] == ["claude"] * len(store.saved)


def test_create_and_on_start_precede_first_check_and_step_saves_before_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_preflight(monkeypatch, baseline="0.151.0")
    events: list[str] = []

    class TrackingStore(MemoryStore):
        def create(self, **kwargs: object) -> Path:
            events.append("create")
            return super().create(**kwargs)

        def save(self, **kwargs: object) -> None:
            events.append("save")
            super().save(**kwargs)

    def check(root: Path, spec: str) -> QualificationResult:
        events.append(f"check:{spec}")
        return qualification(spec.split("@", 1)[1], Verdict.PASS, "check-1")

    def on_start(agent_name: BisectAgent, baseline: str, upper: str, run_dir: Path) -> None:
        events.append(f"on_start:{agent_name}:{baseline}:{upper}")

    def on_step(step: BisectStep) -> None:
        events.append(f"on_step:{step.version}")

    outcome = execute_bisect(
        tmp_path,
        "codex@0.152.0",
        catalog=FakeCatalog(("0.150.0", "0.152.0")),
        summary_store=TrackingStore(),
        check_executor=check,
        bisect_id="bisect-test",
        on_start=on_start,
        on_step=on_step,
    )

    assert events == [
        "create",
        "on_start:codex:0.151.0:0.152.0",
        "check:codex@0.152.0",
        "save",
        "on_step:0.152.0",
        "save",
    ]
    assert outcome.agent_name == "codex"


def test_crashing_check_propagates_and_preserves_truthful_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_preflight(monkeypatch, baseline="0.151.0")
    store = MemoryStore()

    def check(root: Path, spec: str) -> QualificationResult:
        if spec == "codex@0.153.0":
            raise RuntimeError("candidate crashed")
        return qualification(spec.split("@", 1)[1], Verdict.PASS, "check-1")

    with pytest.raises(RuntimeError, match="candidate crashed"):
        execute_bisect(
            tmp_path,
            "codex@0.154.0",
            catalog=FakeCatalog(("0.150.0", "0.152.0", "0.153.0", "0.154.0")),
            summary_store=store,
            check_executor=check,
            bisect_id="bisect-test",
        )

    last_saved = store.saved[-1]
    assert last_saved["stop"] is None
    assert last_saved["first_bad"] is None
