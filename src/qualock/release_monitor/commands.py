from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from packaging.version import Version
from platformdirs import user_cache_dir

from qualock.agents.resolver import CodexResolver
from qualock.baseline.io import assert_suite_fresh, read_baseline_lock
from qualock.commands import CommandError, execute_check
from qualock.project import config_fingerprint, load_project, project_dir, suite_fingerprint
from qualock.qualification.models import QualificationResult, Verdict

from .models import MonitorAction, MonitorOutcome, MonitorState, TerminalVerdict
from .state import FileMonitorStateStore, MonitorStateStore, baseline_sha256


class ReleaseSource(Protocol):
    def latest_version(self) -> str:
        raise NotImplementedError


CheckExecutor = Callable[[Path, str], QualificationResult]


@dataclass(frozen=True)
class MonitorPreflight:
    baseline_version: str
    baseline_sha256: str


def _default_release_source() -> ReleaseSource:
    return CodexResolver(Path(user_cache_dir("qualock")))


def monitor_preflight(root: Path) -> MonitorPreflight:
    config, canaries = load_project(root)
    lock = read_baseline_lock(project_dir(root) / "baseline.lock")
    assert_suite_fresh(lock, suite_fingerprint(canaries), config_fingerprint(config))
    if lock.agent.name != "codex":
        raise CommandError("release monitor supports only a Codex baseline")
    return MonitorPreflight(
        baseline_version=lock.agent.version,
        baseline_sha256=baseline_sha256(lock),
    )


def _join_warning(existing: str | None, new: str) -> str:
    return f"{existing}; {new}" if existing else new


def execute_monitor(
    root: Path,
    *,
    force: bool = False,
    release_source: ReleaseSource | None = None,
    state_store: MonitorStateStore | None = None,
    check_executor: CheckExecutor = execute_check,
) -> MonitorOutcome:
    context = monitor_preflight(root)
    source = release_source or _default_release_source()
    latest = source.latest_version()
    baseline_order = Version(context.baseline_version)
    latest_order = Version(latest)

    if latest_order <= baseline_order:
        return MonitorOutcome(
            action=MonitorAction.NO_NEW_RELEASE,
            baseline_version=context.baseline_version,
            latest_version=latest,
        )

    store = state_store or FileMonitorStateStore()
    state, state_warning = store.load(root)
    matching = (
        state
        if state is not None and state.baseline_sha256 == context.baseline_sha256
        else None
    )

    if matching is not None:
        recorded_order = Version(matching.candidate_version)
        if latest == matching.candidate_version and not force:
            return MonitorOutcome(
                action=MonitorAction.ALREADY_QUALIFIED,
                baseline_version=context.baseline_version,
                latest_version=latest,
                recorded_verdict=Verdict(matching.verdict.value),
                state_warning=state_warning,
            )
        if latest_order < recorded_order:
            return MonitorOutcome(
                action=MonitorAction.NO_DOWNGRADE,
                baseline_version=context.baseline_version,
                latest_version=latest,
                recorded_verdict=Verdict(matching.verdict.value),
                state_warning=state_warning,
            )

    result = check_executor(root, f"codex@{latest}")
    state_persisted: bool | None = None

    if result.verdict is not Verdict.INCOMPLETE:
        terminal_state = MonitorState(
            baseline_sha256=context.baseline_sha256,
            candidate_version=latest,
            verdict=TerminalVerdict(result.verdict.value),
            qualification_id=result.qualification_id,
            completed_at=datetime.now(UTC).isoformat(),
        )
        try:
            store.save(root, terminal_state)
        except Exception as exc:  # noqa: BLE001 - state backends may fail arbitrarily
            state_persisted = False
            state_warning = _join_warning(
                state_warning,
                f"release monitor state could not be saved: {exc}",
            )
        else:
            state_persisted = True

    return MonitorOutcome(
        action=MonitorAction.CHECKED,
        baseline_version=context.baseline_version,
        latest_version=latest,
        qualification_result=result,
        state_persisted=state_persisted,
        state_warning=state_warning,
    )
