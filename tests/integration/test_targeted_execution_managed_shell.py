from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

import qualock.targeted_execution.commands as targeted_commands
from qualock.baseline.io import write_baseline_lock
from qualock.baseline.models import AgentPin, BaselineLock, CanaryStability, ModelPin
from qualock.config.io import write_default_config
from qualock.github_pr.commands import qualify_prepared_pr
from qualock.github_pr.models import PrClassification, PrReportVerdict
from qualock.project import config_fingerprint, load_project, project_dir, suite_fingerprint
from qualock.qualification.models import QualificationResult, Verdict
from qualock.release_monitor.commands import execute_monitor
from qualock.release_monitor.models import MonitorAction
from qualock.targeted_execution.commands import execute_targeted_check
from qualock.targeted_execution.planning import TargetedExecutionNotReady
from qualock.version_bisect.commands import execute_bisect
from qualock.version_bisect.models import BisectStop
from tests.unit.test_commands import FakeBackend, FakeResolver
from tests.unit.test_github_pr_commands import (
    ProjectFixture,
    _context,
    _qualification_result,
)
from tests.unit.test_release_monitor_flow import (
    FakeReleaseSource,
    MemoryStateStore,
    patch_fresh_context,
)
from tests.unit.test_release_monitor_flow import (
    qualification as monitor_qualification,
)
from tests.unit.test_version_bisect_commands import (
    FakeCatalog,
    MemoryStore,
    patch_preflight,
)
from tests.unit.test_version_bisect_commands import (
    qualification as bisect_qualification,
)

RAW_CONTEXT_SENTINEL = "DO-NOT-PERSIST-RAW-CONTEXT"


class ForbiddenResolver:
    def resolve(self, version: str) -> None:
        raise AssertionError("execution boundary crossed")


class ForbiddenBackend:
    def prepare(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("execution boundary crossed")

    def run_attempt(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("execution boundary crossed")


class RecordingBackend(FakeBackend):
    @property
    def prepared_canary_ids(self) -> list[str]:
        return self.prepared

    @property
    def attempt_canary_ids(self) -> list[str]:
        return [canary_id for canary_id, _side, _repetition in self.calls]


def _copy_click_canary(root: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    source_dir = repo_root / "benchmarks/oss-smoke"
    canary_dir = root / ".qualock/canaries"
    graders_dir = canary_dir / "graders"
    graders_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        source_dir / "click-sentinel.yaml",
        canary_dir / "click-sentinel.yaml",
    )
    shutil.copyfile(
        source_dir / "graders/click-sentinel.patch",
        graders_dir / "click-sentinel.patch",
    )


def _write_managed_shell_probe(root: Path) -> None:
    canary_dir = root / ".qualock/canaries"
    (canary_dir / "managed-shell.patch").write_text("patch", encoding="utf-8")
    (canary_dir / "managed-shell-registration-probe.yaml").write_text(
        f"""schema_version: 1
id: managed-shell-registration-probe
name: Managed shell registration probe
repository:
  url: https://example.invalid/managed-shell-probe.git
  base_sha: {'b' * 40}
runtime:
  image: python:3.12-slim
task: Verify managed shell registration.
setup: []
agent:
  timeout_seconds: 60
grader:
  patch: managed-shell.patch
  command:
    - pytest -q
constraints:
  protected_paths:
    - tests/**
critical: true
coverage:
  - contract_id: command.execution
    context_requirements:
      codex.managed.shell_tool: true
      codex.managed.unified_exec: false
""",
        encoding="utf-8",
    )


def _setup_project(root: Path, *, include_probe: bool) -> None:
    qdir = root / ".qualock"
    (qdir / "canaries").mkdir(parents=True)
    (qdir / "results").mkdir()
    write_default_config(qdir / "config.yaml")
    _copy_click_canary(root)
    if include_probe:
        _write_managed_shell_probe(root)


def _write_lock(root: Path, *, version: str = "0.149.1") -> None:
    config, canaries = load_project(root)
    binary = FakeResolver().resolve(version)
    lock = BaselineLock(
        schema_version=1,
        created_at="2026-09-18T00:00:00Z",
        agent=AgentPin(
            name="codex",
            version=version,
            binary_sha256=binary.sha256,
            support_sha256=None,
        ),
        model=ModelPin(
            id=config.model.id,
            snapshot=config.model.snapshot,
            reasoning_effort=config.model.reasoning_effort,
        ),
        qualock_version="0.1.1",
        suite_sha256=suite_fingerprint(canaries),
        config_sha256=config_fingerprint(config),
        canaries={
            canary.id: CanaryStability(valid_runs=3, successes=3)
            for canary in canaries
        },
    )
    write_baseline_lock(project_dir(root) / "baseline.lock", lock)


def _write_signal_context(
    root: Path,
    *,
    include_raw_sentinel: bool,
) -> tuple[Path, Path]:
    signal = root / "signal.yaml"
    signal.write_text(
        """schema_version: 0
agent: codex
baseline_version: "0.149.1"
candidate_version: "0.150.1"
impacts:
  - contract_id: command.execution
    scope_requirements:
      codex.managed.shell_tool: true
      codex.managed.unified_exec: false
""",
        encoding="utf-8",
    )
    raw_line = (
        f'  unrelated.secret: "{RAW_CONTEXT_SENTINEL}"\n'
        if include_raw_sentinel
        else ""
    )
    context = root / "context.yaml"
    context.write_text(
        """schema_version: 0
facts:
  codex.managed.shell_tool: true
  codex.managed.unified_exec: false
"""
        + raw_line,
        encoding="utf-8",
    )
    return signal, context


def test_legacy_click_coverage_gap_stops_before_execution_boundary(tmp_path: Path) -> None:
    _setup_project(tmp_path, include_probe=False)
    _write_lock(tmp_path)
    signal, context = _write_signal_context(
        tmp_path,
        include_raw_sentinel=False,
    )

    with pytest.raises(TargetedExecutionNotReady) as exc_info:
        execute_targeted_check(
            tmp_path,
            signal,
            context,
            resolver=ForbiddenResolver(),
            backend=ForbiddenBackend(),
            qualification_id="target-check-click-gap",
        )

    assessment = exc_info.value.assessment
    assert assessment.status.value == "INCOMPLETE"
    assert [(item.contract_id, item.reason.value) for item in assessment.uncovered] == [
        ("command.execution", "COVERAGE_GAP")
    ]
    assert assessment.selected_sources == ()
    assert not (tmp_path / ".qualock/results/targeted").exists()


def test_explicit_managed_shell_probe_executes_exact_source_and_preserves_privacy(
    tmp_path: Path,
) -> None:
    _setup_project(tmp_path, include_probe=True)
    _write_lock(tmp_path)
    signal, context = _write_signal_context(
        tmp_path,
        include_raw_sentinel=True,
    )
    backend = RecordingBackend({"0.149.1", "0.150.1"})

    outcome = execute_targeted_check(
        tmp_path,
        signal,
        context,
        resolver=FakeResolver(),
        backend=backend,
        qualification_id="target-check-managed-shell",
    )

    assert outcome.assessment.selected_sources == (
        "managed-shell-registration-probe",
    )
    assert tuple(item.canary_id for item in outcome.result.executions) == (
        "managed-shell-registration-probe",
    )
    assert backend.prepared_canary_ids == [
        "managed-shell-registration-probe",
    ]
    assert set(backend.attempt_canary_ids) == {
        "managed-shell-registration-probe",
    }
    assert "click-sentinel-duplication" not in backend.prepared_canary_ids
    assert "click-sentinel-duplication" not in backend.attempt_canary_ids
    assert outcome.receipt.selected_sources == (
        "managed-shell-registration-probe",
    )
    assert outcome.result_dir.parent.name == "targeted"

    for artifact in outcome.result_dir.iterdir():
        if artifact.is_file():
            assert RAW_CONTEXT_SENTINEL not in artifact.read_text(
                encoding="utf-8",
                errors="ignore",
            )


def _forbid_targeted_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("independent flow must not integrate targeted execution")

    monkeypatch.setattr(targeted_commands, "execute_targeted_check", forbidden)


def test_release_monitor_does_not_integrate_targeted_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_targeted_execution(monkeypatch)
    patch_fresh_context(monkeypatch)
    calls: list[str] = []

    def check(root: Path, candidate_spec: str) -> QualificationResult:
        calls.append(candidate_spec)
        return monitor_qualification(Verdict.PASS)

    outcome = execute_monitor(
        tmp_path,
        release_source=FakeReleaseSource("0.152.0"),
        state_store=MemoryStateStore(),
        check_executor=check,
    )

    assert calls == ["codex@0.152.0"]
    assert outcome.action is MonitorAction.CHECKED


def test_github_pr_qualification_does_not_integrate_targeted_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_targeted_execution(monkeypatch)
    fixture = ProjectFixture(root=tmp_path)
    context = _context(PrClassification.UPGRADE, agent="codex")
    raw = fixture.proposed_lock_json()
    calls: list[tuple[Path, str, Any]] = []

    def check_executor(
        root: Path,
        candidate_spec: str,
        *,
        resolver: Any = None,
    ) -> QualificationResult:
        calls.append((root, candidate_spec, resolver))
        return _qualification_result(Verdict.PASS)

    report = qualify_prepared_pr(
        fixture.root,
        context,
        raw,
        credential_available=True,
        resolver=fixture.resolver,
        check_executor=check_executor,
    )

    assert len(calls) == 1
    assert calls[0][0] == fixture.root
    assert calls[0][1] == f"codex@{fixture.candidate_version}"
    assert report.verdict is PrReportVerdict.PASS


def test_version_bisect_does_not_integrate_targeted_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_targeted_execution(monkeypatch)
    patch_preflight(monkeypatch, baseline="0.151.0")
    calls: list[str] = []

    def check(root: Path, spec: str) -> QualificationResult:
        calls.append(spec)
        return bisect_qualification(
            spec.split("@", 1)[1],
            Verdict.PASS,
            "check-bisect-targeted-isolation",
        )

    outcome = execute_bisect(
        tmp_path,
        "codex@0.152.0",
        catalog=FakeCatalog(("0.152.0",)),
        summary_store=MemoryStore(),
        check_executor=check,
        bisect_id="bisect-targeted-isolation",
    )

    assert calls == ["codex@0.152.0"]
    assert outcome.stop_reason is BisectStop.NO_BAD_FOUND
