import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from qualock.agents.releases import StableReleaseCatalog, default_stable_release_catalog
from qualock.baseline.io import assert_suite_fresh, read_baseline_lock
from qualock.commands import CommandError, execute_check, parse_agent_spec
from qualock.project import config_fingerprint, load_project, project_dir, suite_fingerprint
from qualock.qualification.models import QualificationResult, Verdict

from .models import BisectAgent, BisectOutcome, BisectStep, BisectStop
from .storage import BisectSummaryStore, FileBisectSummaryStore

CheckExecutor = Callable[[Path, str], QualificationResult]
OnStart = Callable[[BisectAgent, str, str, Path], None]
OnStep = Callable[[BisectStep], None]

_STABLE_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True)
class BisectPreflight:
    agent_name: BisectAgent
    baseline_version: str


def _version_key(version: str) -> tuple[int, int, int]:
    match = _STABLE_VERSION_RE.fullmatch(version)
    if match is None:
        raise CommandError(f"not a stable version: {version!r}")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _bisect_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"bisect-{stamp}-{uuid.uuid4().hex[:8]}"


def _default_store(root: Path) -> BisectSummaryStore:
    return FileBisectSummaryStore(project_dir(root) / "results")


def bisect_preflight(root: Path) -> BisectPreflight:
    config, canaries = load_project(root)
    lock = read_baseline_lock(project_dir(root) / "baseline.lock")
    assert_suite_fresh(lock, suite_fingerprint(canaries), config_fingerprint(config))
    if lock.agent.name != config.agent.name:
        raise CommandError(
            f"config agent {config.agent.name} does not match baseline agent {lock.agent.name}"
        )
    agent_name: BisectAgent
    if lock.agent.name == "codex":
        agent_name = "codex"
    elif lock.agent.name == "claude":
        agent_name = "claude"
    else:
        raise CommandError(f"version bisect does not support agent {lock.agent.name!r}")
    _version_key(lock.agent.version)
    return BisectPreflight(agent_name=agent_name, baseline_version=lock.agent.version)


def execute_bisect(
    root: Path,
    upper_spec: str,
    *,
    catalog: StableReleaseCatalog | None = None,
    summary_store: BisectSummaryStore | None = None,
    check_executor: CheckExecutor = execute_check,
    bisect_id: str | None = None,
    on_start: OnStart | None = None,
    on_step: OnStep | None = None,
) -> BisectOutcome:
    upper_name, upper_version = parse_agent_spec(upper_spec)
    _version_key(upper_version)

    context = bisect_preflight(root)

    if upper_name != context.agent_name:
        raise CommandError(
            f"upper bound agent {upper_name} does not match baseline agent {context.agent_name}"
        )

    frozen_catalog = tuple(
        (catalog or default_stable_release_catalog(context.agent_name)).stable_versions()
    )
    if upper_version not in frozen_catalog:
        raise CommandError(
            f"{context.agent_name}@{upper_version} is not a published stable release"
        )
    if _version_key(upper_version) <= _version_key(context.baseline_version):
        raise CommandError("upper bound must be numerically newer than the locked baseline")

    candidates = tuple(
        version
        for version in sorted(set(frozen_catalog), key=_version_key)
        if _version_key(context.baseline_version) < _version_key(version) <= _version_key(upper_version)
    )

    bid = bisect_id or _bisect_id()
    store = summary_store or _default_store(root)

    run_dir = store.create(
        bisect_id=bid,
        baseline=context.baseline_version,
        upper=upper_version,
        candidates=candidates,
        steps=(),
        last_good=context.baseline_version,
        first_bad=None,
        stop=None,
        agent=context.agent_name,
    )
    if on_start is not None:
        on_start(context.agent_name, context.baseline_version, upper_version, run_dir)

    steps: list[BisectStep] = []
    last_known_good = context.baseline_version
    first_bad: str | None = None
    stop_reason: BisectStop | None = None

    for version in candidates:
        result = check_executor(root, f"{context.agent_name}@{version}")
        step = BisectStep(
            version=version,
            qualification_id=result.qualification_id,
            verdict=result.verdict,
        )
        steps.append(step)

        if result.verdict is Verdict.PASS:
            last_known_good = version
            first_bad = None
            stop_reason = None
        elif result.verdict is Verdict.BLOCK:
            first_bad = version
            stop_reason = BisectStop.FIRST_BAD_FOUND
        elif result.verdict is Verdict.WARN:
            first_bad = None
            stop_reason = BisectStop.WARN_UNRESOLVED
        elif result.verdict is Verdict.INCOMPLETE:
            first_bad = None
            stop_reason = BisectStop.INCOMPLETE
        else:
            raise AssertionError(f"unsupported qualification verdict: {result.verdict!r}")

        store.save(
            bisect_id=bid,
            baseline=context.baseline_version,
            upper=upper_version,
            candidates=candidates,
            steps=tuple(steps),
            last_good=last_known_good,
            first_bad=first_bad,
            stop=stop_reason,
            agent=context.agent_name,
        )
        if on_step is not None:
            on_step(step)

        if stop_reason is not None:
            return BisectOutcome(
                bisect_id=bid,
                agent_name=context.agent_name,
                baseline_version=context.baseline_version,
                upper_version=upper_version,
                steps=tuple(steps),
                last_known_good=last_known_good,
                first_bad=first_bad,
                stop_reason=stop_reason,
            )

    stop_reason = BisectStop.NO_BAD_FOUND
    store.save(
        bisect_id=bid,
        baseline=context.baseline_version,
        upper=upper_version,
        candidates=candidates,
        steps=tuple(steps),
        last_good=last_known_good,
        first_bad=first_bad,
        stop=stop_reason,
        agent=context.agent_name,
    )
    return BisectOutcome(
        bisect_id=bid,
        agent_name=context.agent_name,
        baseline_version=context.baseline_version,
        upper_version=upper_version,
        steps=tuple(steps),
        last_known_good=last_known_good,
        first_bad=first_bad,
        stop_reason=stop_reason,
    )
