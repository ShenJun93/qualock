from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from packaging.version import Version

from qualock.agents.orchestration import orchestration_capabilities
from qualock.agents.releases import (
    LatestReleaseSource,
    default_latest_release_source,
)
from qualock.baseline.io import assert_suite_fresh, read_baseline_lock
from qualock.commands import CommandError, execute_check
from qualock.project import config_fingerprint, load_project, project_dir, suite_fingerprint
from qualock.qualification.models import QualificationResult, Verdict

from .models import MonitorAction, MonitorOutcome, MonitorState, TerminalVerdict
from .state import FileMonitorStateStore, MonitorStateStore, baseline_sha256

CheckExecutor = Callable[[Path, str], QualificationResult]
MonitorAgent = Literal["codex", "claude", "gemini"]


@dataclass(frozen=True)
class MonitorPreflight:
    agent_name: MonitorAgent
    baseline_version: str
    baseline_sha256: str


def monitor_preflight(root: Path) -> MonitorPreflight:
    config, canaries = load_project(root)
    lock = read_baseline_lock(project_dir(root) / "baseline.lock")
    assert_suite_fresh(lock, suite_fingerprint(canaries), config_fingerprint(config))
    agent_name = lock.agent.name
    if config.agent.name != agent_name:
        raise CommandError("baseline agent does not match configured agent")
    if agent_name == "antigravity":
        raise CommandError(
            "release monitor is unavailable for Antigravity because "
            "QuaLock does not discover Antigravity releases"
        )
    if not orchestration_capabilities(agent_name).release_discovery:
        raise CommandError(f"release monitor does not support agent {agent_name!r}")
    return MonitorPreflight(
        agent_name=agent_name,
        baseline_version=lock.agent.version,
        baseline_sha256=baseline_sha256(lock),
    )


def _join_warning(existing: str | None, new: str) -> str:
    return f"{existing}; {new}" if existing else new


def execute_monitor(
    root: Path,
    *,
    force: bool = False,
    release_source: LatestReleaseSource | None = None,
    state_store: MonitorStateStore | None = None,
    check_executor: CheckExecutor = execute_check,
) -> MonitorOutcome:
    context = monitor_preflight(root)
    source = release_source or default_latest_release_source(context.agent_name)
    latest = source.latest_version()
    baseline_order = Version(context.baseline_version)
    latest_order = Version(latest)

    if latest_order <= baseline_order:
        return MonitorOutcome(
            action=MonitorAction.NO_NEW_RELEASE,
            agent_name=context.agent_name,
            baseline_version=context.baseline_version,
            latest_version=latest,
        )

    store = state_store or FileMonitorStateStore()
    state, state_warning = store.load(root)
    matching = (
        state
        if (
            state is not None
            and state.baseline_sha256 == context.baseline_sha256
            and state.agent == context.agent_name
        )
        else None
    )

    if matching is not None:
        recorded_order = Version(matching.candidate_version)
        if latest == matching.candidate_version and not force:
            return MonitorOutcome(
                action=MonitorAction.ALREADY_QUALIFIED,
                agent_name=context.agent_name,
                baseline_version=context.baseline_version,
                latest_version=latest,
                recorded_verdict=Verdict(matching.verdict.value),
                state_warning=state_warning,
            )
        if latest_order < recorded_order:
            return MonitorOutcome(
                action=MonitorAction.NO_DOWNGRADE,
                agent_name=context.agent_name,
                baseline_version=context.baseline_version,
                latest_version=latest,
                recorded_verdict=Verdict(matching.verdict.value),
                state_warning=state_warning,
            )

    result = check_executor(root, f"{context.agent_name}@{latest}")
    state_persisted: bool | None = None

    if result.verdict is not Verdict.INCOMPLETE:
        terminal_state = MonitorState(
            baseline_sha256=context.baseline_sha256,
            agent=context.agent_name,
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
        agent_name=context.agent_name,
        baseline_version=context.baseline_version,
        latest_version=latest,
        qualification_result=result,
        state_persisted=state_persisted,
        state_warning=state_warning,
    )
