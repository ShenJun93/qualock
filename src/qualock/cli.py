import os
import shutil
from pathlib import Path
from typing import Annotated, Literal, NoReturn

import typer
from packaging.version import Version
from pydantic import ValidationError
from rich.console import Console

from qualock.agents.resolver import CodexResolveError
from qualock.baseline.io import BaselineStaleError, read_baseline_lock
from qualock.canary.loader import CanaryLoadError
from qualock.commands import (
    BaselineUnstableError,
    CommandError,
    execute_baseline,
    execute_check,
)
from qualock.config.io import ConfigError, write_default_config
from qualock.github_pr.commands import prepare_pr, qualify_prepared_pr
from qualock.github_pr.publisher import (
    GitHubPublishError,
    HttpxGitHubPublisher,
    publish_pr_report,
)
from qualock.github_pr.report import (
    PrArtifactError,
    read_context,
    read_report,
    write_context,
    write_report,
)
from qualock.github_pr.setup import GitHubSetupConflictError, install_github_workflows
from qualock.github_pr.source import HttpxGitHubPrSource, PrContextError
from qualock.project import load_project, project_dir
from qualock.project_protection.commands import (
    ProjectProtectionConfigError,
)
from qualock.project_protection.commands import (
    execute_protect as execute_project_protect,
)
from qualock.project_protection.commands import (
    execute_verify as execute_project_verify,
)
from qualock.project_protection.models import ProtectionStatus
from qualock.project_protection.render import render_protect_terminal, render_verify_terminal
from qualock.project_protection.runner import ProjectProtectionError
from qualock.project_protection.signing import ProjectLockIntegrityError
from qualock.project_setup.commands import (
    SetupReadinessError,
    SetupUnsupportedError,
    apply_setup_plan,
    build_setup_plan,
)
from qualock.project_setup.models import ProtectionLevel, ReadinessStatus
from qualock.project_setup.render import render_setup_plan
from qualock.project_start.commands import (
    StartStateChangedError,
    StartStateError,
    apply_start_bootstrap,
    prepare_start,
)
from qualock.project_start.models import StartProjectState
from qualock.project_watch.control import WatchControlChangedError
from qualock.project_watch.engine import run_watch as run_project_watch
from qualock.project_watch.models import WatchEvent, WatchEventKind
from qualock.project_watch.render import render_watch_event
from qualock.project_watch.snapshot import ProjectWatchSnapshotError
from qualock.qualification.models import QualificationResult, Verdict
from qualock.release_monitor.commands import execute_monitor
from qualock.release_monitor.models import MonitorAction
from qualock.report.render import render_safety_terminal, render_terminal
from qualock.report.safety import build_safety_summary
from qualock.run.docker import DockerRunner
from qualock.scheduler.backends import (
    SchedulerOperationalError,
    SchedulerUnsupportedError,
)
from qualock.scheduler.commands import (
    ScheduleOutcome,
    ScheduleStatus,
    disable_schedule,
    enable_schedule,
    schedule_status,
)
from qualock.version_bisect.commands import execute_bisect
from qualock.version_bisect.models import BisectOutcome, BisectStep, BisectStop

app = typer.Typer(no_args_is_help=True, add_completion=False)
schedule_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(schedule_app, name="schedule")
github_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(github_app, name="github")
console = Console()


def _render_safety_result(root: Path, result: QualificationResult) -> None:
    try:
        _config, canaries = load_project(root)
        display_names = {canary.id: canary.name for canary in canaries}
    except (ConfigError, CanaryLoadError, FileNotFoundError):
        display_names = {}
    summary = build_safety_summary(result, display_names)
    evidence_path = f".qualock/results/{result.qualification_id}/"
    console.print(render_safety_terminal(summary, evidence_path), end="", markup=False)


def _monitor_check_executor(root: Path, candidate_spec: str) -> QualificationResult:
    version = candidate_spec.rsplit("@", 1)[1]
    lock = read_baseline_lock(project_dir(root) / "baseline.lock")
    console.print(f"Baseline: Codex {lock.agent.version}", markup=False)
    console.print(f"Latest:   Codex {version}", markup=False)
    console.print(
        f"\nNew Codex release found. Qualifying {version} against baseline "
        f"{lock.agent.version}.",
        markup=False,
    )
    return execute_check(root, candidate_spec)


@app.command("init")
def init_command() -> None:
    root = Path.cwd()
    ub = project_dir(root)
    (ub / "canaries").mkdir(parents=True, exist_ok=True)
    (ub / "results").mkdir(parents=True, exist_ok=True)
    config_path = ub / "config.yaml"
    if not config_path.exists():
        write_default_config(config_path)
    ignore_path = ub / ".gitignore"
    if not ignore_path.exists():
        ignore_path.write_text("results/\nwork/\n", encoding="utf-8")
    console.print("Created .qualock/config.yaml, canaries/, results/")


@app.command("doctor")
def doctor_command() -> None:
    root = Path.cwd()
    try:
        _config, canaries = load_project(root)
    except (ConfigError, CanaryLoadError, FileNotFoundError) as exc:
        console.print(f"Config  FAIL  {exc}")
        raise typer.Exit(3) from exc

    checks = {
        "Git": shutil.which("git") is not None,
        "npm": shutil.which("npm") is not None,
        "Docker": DockerRunner().daemon_ready(),
        "Canaries": bool(canaries),
    }
    for name, ok in checks.items():
        console.print(f"{name:<10} {'PASS' if ok else 'FAIL'}")
    if not all(checks.values()):
        raise typer.Exit(1)


@app.command("baseline")
def baseline_command(agent: str) -> None:
    try:
        lock = execute_baseline(Path.cwd(), agent)
    except (ConfigError, CanaryLoadError, CommandError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(3) from exc
    except BaselineUnstableError as exc:
        console.print(str(exc))
        raise typer.Exit(4) from exc
    except Exception as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc
    console.print(f"Baseline pinned: Codex {lock.agent.version}")


@app.command("check")
def check_command(
    candidate: str,
    technical: bool = typer.Option(
        False,
        "--technical",
        help="Show the technical qualification report instead of the safety summary.",
    ),
) -> None:
    root = Path.cwd()
    try:
        result = execute_check(root, candidate)
    except (ConfigError, CanaryLoadError, CommandError, FileNotFoundError) as exc:
        console.print(str(exc))
        raise typer.Exit(3) from exc
    except BaselineStaleError as exc:
        console.print(str(exc))
        raise typer.Exit(4) from exc
    except Exception as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc

    if technical:
        console.print(render_terminal(result), end="")
    else:
        _render_safety_result(root, result)

    if result.verdict is Verdict.BLOCK:
        raise typer.Exit(2)
    if result.verdict is Verdict.INCOMPLETE:
        raise typer.Exit(4)


@app.command("monitor")
def monitor_command(
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Re-run the matching newer Codex release if it was already qualified.",
        ),
    ] = False,
) -> None:
    root = Path.cwd()
    console.print("QuaLock Release Monitor\n", end="", markup=False)
    try:
        outcome = execute_monitor(
            root,
            force=force,
            check_executor=_monitor_check_executor,
        )
    except (ConfigError, CanaryLoadError, CommandError, FileNotFoundError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(3) from exc
    except BaselineStaleError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(4) from exc
    except Exception as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc

    if outcome.action is not MonitorAction.CHECKED:
        console.print(f"Baseline: Codex {outcome.baseline_version}", markup=False)
        console.print(f"Latest:   Codex {outcome.latest_version}", markup=False)
    if outcome.state_warning:
        console.print(f"Warning: {outcome.state_warning}", markup=False)

    if outcome.action is MonitorAction.NO_NEW_RELEASE:
        if Version(outcome.latest_version) < Version(outcome.baseline_version):
            console.print(
                "Your baseline is newer than npm latest. No downgrade qualification was run.",
                markup=False,
            )
        else:
            console.print("No newer Codex release needs qualification.", markup=False)
        return
    if outcome.action is MonitorAction.NO_DOWNGRADE:
        console.print(
            "A newer candidate was already qualified; no downgrade check was run.",
            markup=False,
        )
        return
    if outcome.action is MonitorAction.ALREADY_QUALIFIED:
        if outcome.recorded_verdict is None:
            console.print("Release monitor state is missing its verdict.", markup=False)
            raise typer.Exit(1)
        console.print(
            f"Codex {outcome.latest_version} was already qualified for this baseline: "
            f"{outcome.recorded_verdict.value.upper()}",
            markup=False,
        )
        console.print("Run `qualock monitor --force` to qualify it again.", markup=False)
        if outcome.recorded_verdict is Verdict.BLOCK:
            raise typer.Exit(2)
        return

    result = outcome.qualification_result
    if result is None:
        console.print("Release monitor check result is missing.", markup=False)
        raise typer.Exit(1)
    _render_safety_result(root, result)
    if result.verdict is Verdict.BLOCK:
        raise typer.Exit(2)
    if result.verdict is Verdict.INCOMPLETE:
        raise typer.Exit(4)


def _print_bisect_start(baseline: str, upper: str, run_dir: Path) -> None:
    del run_dir
    console.print(f"Baseline: Codex {baseline}", markup=False)
    console.print(f"Searching through: {upper}\n", markup=False)


def _print_bisect_step(step: BisectStep) -> None:
    console.print(f"{step.version}  {step.verdict.value.upper()}", markup=False)


def _render_bisect_terminal(outcome: BisectOutcome) -> None:
    evidence_path = f".qualock/results/{outcome.bisect_id}/"
    if outcome.stop_reason is BisectStop.FIRST_BAD_FOUND:
        assert outcome.first_bad is not None
        console.print("FIRST BAD RELEASE", markup=False)
        console.print(f"Codex {outcome.first_bad}", markup=False)
        console.print(f"Last known good: Codex {outcome.last_known_good}", markup=False)
        console.print(f"Evidence: {evidence_path}", markup=False)
        raise typer.Exit(2)

    if outcome.stop_reason is BisectStop.NO_BAD_FOUND:
        console.print(
            f"No confirmed bad release found through Codex {outcome.upper_version}.",
            markup=False,
        )
        console.print(f"Last known good: Codex {outcome.last_known_good}", markup=False)
        console.print(f"Evidence: {evidence_path}", markup=False)
        return

    last_step = outcome.steps[-1]
    console.print("SEARCH STOPPED", markup=False)
    console.print(f"{last_step.version}  {last_step.verdict.value.upper()}", markup=False)
    console.print("No first bad release was claimed.", markup=False)
    console.print(f"Evidence: {evidence_path}", markup=False)
    raise typer.Exit(4)


@app.command("bisect")
def bisect_command(upper: str) -> None:
    root = Path.cwd()
    console.print("QuaLock Version Bisect\n", end="", markup=False)
    try:
        outcome = execute_bisect(
            root,
            upper,
            on_start=_print_bisect_start,
            on_step=_print_bisect_step,
        )
    except (ConfigError, CanaryLoadError, CommandError, FileNotFoundError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(3) from exc
    except BaselineStaleError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(4) from exc
    except CodexResolveError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc
    except Exception as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc

    _render_bisect_terminal(outcome)


def _render_schedule_outcome(
    outcome: ScheduleOutcome,
    *,
    mode: Literal["enable", "status", "disable"],
) -> None:
    lines = [
        "QuaLock Release Schedule",
        "",
        outcome.status.value,
        "",
        f"Project: {outcome.project_root}",
    ]
    registration = outcome.registration
    if mode == "enable" and registration is not None:
        lines.append(
            f"Runs: every day at {registration.hour:02d}:"
            f"{registration.minute:02d} local time"
        )
    elif mode == "status" and registration is not None:
        lines.append(
            f"Daily time: {registration.hour:02d}:"
            f"{registration.minute:02d} local time"
        )
    lines.append(f"Backend: {outcome.backend_label}")
    if mode == "status" and registration is not None:
        lines.append(f"Python: {registration.python_executable}")
    lines.append(f"Logs: {outcome.log_path}")
    if outcome.detail:
        lines.extend(["", outcome.detail])
    if outcome.status is ScheduleStatus.NEEDS_REPAIR:
        lines.extend(
            [
                "",
                (
                    "Run `qualock schedule enable` to repair it or "
                    "`qualock schedule disable` to remove it."
                ),
            ]
        )
    if mode == "enable":
        lines.extend(
            [
                "",
                "The scheduled job only runs `qualock monitor`.",
                "It does not update Codex or change your baseline.",
            ]
        )
    console.print("\n".join(lines) + "\n", end="", markup=False, soft_wrap=True)


def _schedule_fail(error: Exception, code: int) -> NoReturn:
    console.print(str(error), markup=False, soft_wrap=True)
    raise typer.Exit(code)


@schedule_app.command("enable")
def schedule_enable_command(
    at: Annotated[str, typer.Option("--at")] = "09:00",
) -> None:
    try:
        outcome = enable_schedule(Path.cwd(), at)
    except BaselineStaleError as exc:
        _schedule_fail(exc, 4)
    except SchedulerUnsupportedError as exc:
        _schedule_fail(exc, 3)
    except (
        ConfigError,
        CanaryLoadError,
        CommandError,
        FileNotFoundError,
        ValidationError,
        ValueError,
    ) as exc:
        _schedule_fail(exc, 3)
    except SchedulerOperationalError as exc:
        _schedule_fail(exc, 1)
    except Exception as exc:  # noqa: BLE001 - unexpected scheduler failures are operational
        _schedule_fail(exc, 1)
    _render_schedule_outcome(outcome, mode="enable")


@schedule_app.command("status")
def schedule_status_command() -> None:
    try:
        outcome = schedule_status(Path.cwd())
    except SchedulerUnsupportedError as exc:
        _schedule_fail(exc, 3)
    except SchedulerOperationalError as exc:
        _schedule_fail(exc, 1)
    except Exception as exc:  # noqa: BLE001 - unexpected scheduler failures are operational
        _schedule_fail(exc, 1)
    _render_schedule_outcome(outcome, mode="status")
    if outcome.status is ScheduleStatus.NEEDS_REPAIR:
        raise typer.Exit(4)


@schedule_app.command("disable")
def schedule_disable_command() -> None:
    try:
        outcome = disable_schedule(Path.cwd())
    except SchedulerUnsupportedError as exc:
        _schedule_fail(exc, 3)
    except SchedulerOperationalError as exc:
        _schedule_fail(exc, 1)
    except Exception as exc:  # noqa: BLE001 - unexpected scheduler failures are operational
        _schedule_fail(exc, 1)
    _render_schedule_outcome(outcome, mode="disable")


@app.command("setup")
def setup_command(
    level: Annotated[
        ProtectionLevel,
        typer.Option("--level", help="Protection level: minimal, recommended, or strong."),
    ] = ProtectionLevel.RECOMMENDED,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Apply the recommended protections without asking for confirmation.",
        ),
    ] = False,
) -> None:
    root = Path.cwd()
    try:
        plan = build_setup_plan(root, level)
    except SetupUnsupportedError as exc:
        console.print(str(exc))
        raise typer.Exit(3) from exc

    console.print(render_setup_plan(plan), end="", markup=False)
    if plan.readiness.status is ReadinessStatus.NEEDS_SETUP:
        raise typer.Exit(4)
    if not yes and not typer.confirm("Apply these protections and protect this project?"):
        console.print("Setup cancelled. No files changed.")
        return

    try:
        result = apply_setup_plan(root, plan)
    except (ProjectLockIntegrityError, SetupReadinessError) as exc:
        console.print(str(exc))
        raise typer.Exit(4) from exc
    except (ConfigError, ProjectProtectionConfigError, FileNotFoundError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(3) from exc
    except ProjectProtectionError as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc

    evidence_path = f".qualock/results/{result.operation_id}/"
    console.print(render_protect_terminal(result, evidence_path), end="", markup=False)
    if result.status is not ProtectionStatus.PASS:
        raise typer.Exit(4)


@app.command("protect")
def protect_command() -> None:
    root = Path.cwd()
    try:
        result = execute_project_protect(root)
    except ProjectLockIntegrityError as exc:
        console.print(str(exc))
        raise typer.Exit(4) from exc
    except (ConfigError, ProjectProtectionConfigError, FileNotFoundError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(3) from exc
    except ProjectProtectionError as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc
    evidence_path = f".qualock/results/{result.operation_id}/"
    console.print(render_protect_terminal(result, evidence_path), end="", markup=False)
    if result.status is not ProtectionStatus.PASS:
        raise typer.Exit(4)


@app.command("verify")
def verify_command() -> None:
    root = Path.cwd()
    try:
        result = execute_project_verify(root)
    except ProjectLockIntegrityError as exc:
        console.print(str(exc))
        raise typer.Exit(4) from exc
    except (ConfigError, ProjectProtectionConfigError, FileNotFoundError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(3) from exc
    except ProjectProtectionError as exc:
        console.print(str(exc))
        raise typer.Exit(1) from exc
    evidence_path = f".qualock/results/{result.operation_id}/"
    console.print(render_verify_terminal(result, evidence_path), end="", markup=False)
    if result.status is ProtectionStatus.FAIL:
        raise typer.Exit(2)
    if result.status is ProtectionStatus.INCOMPLETE:
        raise typer.Exit(4)


def _print_watch_event(event: WatchEvent) -> None:
    if event.kind is WatchEventKind.RESULT:
        if event.result is None:
            return
        evidence_path = f".qualock/results/{event.result.operation_id}/"
        console.print(
            render_verify_terminal(event.result, evidence_path),
            end="",
            markup=False,
        )
        return
    text = render_watch_event(event)
    if text:
        console.print(text, end="", markup=False)


def _run_watch_cli(root: Path) -> None:
    console.print("QuaLock Watch\n", end="", markup=False)
    try:
        outcome = run_project_watch(root, on_event=_print_watch_event)
    except FileNotFoundError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(3) from exc
    except (ProjectLockIntegrityError, WatchControlChangedError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(4) from exc
    except (ProjectWatchSnapshotError, ProjectProtectionError, OSError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc

    if outcome.exit_status is ProtectionStatus.FAIL:
        raise typer.Exit(2)
    if outcome.exit_status is ProtectionStatus.INCOMPLETE or outcome.exit_status is None:
        raise typer.Exit(4)


@app.command("watch")
def watch_command() -> None:
    _run_watch_cli(Path.cwd())


@app.command("start")
def start_command(
    level: Annotated[
        ProtectionLevel,
        typer.Option("--level", help="Protection level: minimal, recommended, or strong."),
    ] = ProtectionLevel.RECOMMENDED,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Trust the current state without asking for bootstrap confirmation.",
        ),
    ] = False,
) -> None:
    root = Path.cwd()
    console.print("QuaLock\n", end="", markup=False)
    try:
        plan = prepare_start(root, level)
    except (SetupUnsupportedError, StartStateError, ConfigError, ProjectProtectionConfigError, FileNotFoundError, ValueError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(3) from exc
    except OSError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc

    if plan.state is StartProjectState.LOCKED:
        _run_watch_cli(root)
        return

    if plan.state is StartProjectState.UNCONFIGURED:
        if plan.setup_plan is None:
            console.print("QuaLock start plan is missing setup information.", markup=False)
            raise typer.Exit(1)
        console.print(render_setup_plan(plan.setup_plan), end="", markup=False)
        if plan.setup_plan.readiness.status is ReadinessStatus.NEEDS_SETUP:
            raise typer.Exit(4)
        confirmation = "Protect the current state and start watching?"
    else:
        console.print("Existing protections found:", markup=False)
        for protection in plan.configured_protections:
            console.print(f"- {protection.name}", markup=False)
        console.print("\nNo trusted baseline exists yet.", markup=False)
        confirmation = "Protect this state and start watching?"

    console.print(
        "\nQuaLock will trust the project's CURRENT state only if every protected check passes.",
        markup=False,
    )
    if not yes and not typer.confirm(confirmation):
        console.print("Start cancelled. No files changed.", markup=False)
        return

    try:
        bootstrap = apply_start_bootstrap(root, plan)
    except (ProjectLockIntegrityError, SetupReadinessError, StartStateChangedError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(4) from exc
    except (SetupUnsupportedError, StartStateError, ConfigError, ProjectProtectionConfigError, FileNotFoundError, ValueError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(3) from exc
    except (ProjectProtectionError, OSError) as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from exc

    result = bootstrap.protect_result
    if result is None:
        console.print("QuaLock could not establish a project baseline.", markup=False)
        raise typer.Exit(1)
    evidence_path = f".qualock/results/{result.operation_id}/"
    console.print(render_protect_terminal(result, evidence_path), end="", markup=False)
    if result.status is not ProtectionStatus.PASS or not result.lock_created:
        raise typer.Exit(4)

    _run_watch_cli(root)


@app.command("report")
def report_command() -> None:
    results = project_dir(Path.cwd()) / "results"
    candidates = [path for path in results.iterdir() if path.is_dir() and (path / "report.md").is_file()]
    if not candidates:
        console.print("No qualification reports found")
        raise typer.Exit(1)
    latest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    console.print((latest / "report.md").read_text(encoding="utf-8"), end="")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise CommandError(f"required workflow environment is missing: {name}")
    return value


def _trusted_canary_display_names(root: Path) -> dict[str, str]:
    try:
        _config, canaries = load_project(root)
    except (ConfigError, CanaryLoadError, FileNotFoundError):
        return {}
    return {canary.id: canary.name for canary in canaries}


@github_app.command("setup")
def github_setup_command() -> None:
    root = Path.cwd()
    try:
        outcome = install_github_workflows(root)
    except GitHubSetupConflictError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(3) from exc

    console.print(f"Producer workflow: {outcome.producer_path}", markup=False, soft_wrap=True)
    console.print(f"Reporter workflow: {outcome.reporter_path}", markup=False, soft_wrap=True)
    console.print(
        "\nCommit these workflow files to your repository, then create a repository "
        "secret named QUALOCK_CODEX_AUTH_B64 containing your base64-encoded Codex "
        "auth.json. You can produce it with:\n",
        markup=False,
        soft_wrap=True,
    )
    console.print(
        "python -c \"import base64,pathlib; "
        "p=pathlib.Path.home()/'.codex/auth.json'; "
        "print(base64.b64encode(p.read_bytes()).decode())\"",
        markup=False,
        soft_wrap=True,
    )
    console.print(
        "\nQualification results are published to the `qualock/pr` status check. "
        "You may optionally configure branch protection to require it before merging.",
        markup=False,
        soft_wrap=True,
    )


@github_app.command("prepare-pr", hidden=True)
def github_prepare_pr_command(
    event: Annotated[Path, typer.Option("--event")],
    context_out: Annotated[Path, typer.Option("--context-out")],
    report_out: Annotated[Path, typer.Option("--report-out")],
    proposed_lock_out: Annotated[Path, typer.Option("--proposed-lock-out")],
) -> None:
    try:
        token = _required_env("GITHUB_TOKEN")
        run_id_raw = _required_env("GITHUB_RUN_ID")
        repository = _required_env("GITHUB_REPOSITORY")
        run_id = int(run_id_raw)
    except CommandError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(3) from exc
    except ValueError as exc:
        console.print("GITHUB_RUN_ID must be an integer", markup=False)
        raise typer.Exit(3) from exc

    source = HttpxGitHubPrSource(token=token)
    try:
        outcome = prepare_pr(
            Path.cwd(),
            event,
            source=source,
            producer_run_id=run_id,
            expected_repository=repository,
        )
        write_context(context_out, outcome.context)
        if outcome.terminal_report is not None:
            write_report(report_out, outcome.terminal_report)
        if outcome.proposed_lock is not None:
            proposed_lock_out.write_bytes(outcome.proposed_lock)
    except (PrContextError, PrArtifactError, OSError) as exc:
        del exc
        console.print("GitHub PR context could not be established", markup=False)
        raise typer.Exit(1) from None

    console.print(outcome.context.classification.value, markup=False)


def _read_bounded_file(path: Path, *, max_bytes: int) -> bytes:
    if path.stat().st_size > max_bytes:
        raise CommandError(f"workflow input is too large: {path.name}")
    return path.read_bytes()


@github_app.command("qualify-pr", hidden=True)
def github_qualify_pr_command(
    context: Annotated[Path, typer.Option("--context")],
    proposed_lock: Annotated[Path, typer.Option("--proposed-lock")],
    report_out: Annotated[Path, typer.Option("--report-out")],
    credential_available: Annotated[str, typer.Option("--credential-available")],
) -> None:
    if credential_available not in {"true", "false"}:
        console.print("--credential-available must be true or false", markup=False)
        raise typer.Exit(3)

    try:
        result = qualify_prepared_pr(
            Path.cwd(),
            read_context(context),
            _read_bounded_file(proposed_lock, max_bytes=131_072),
            credential_available=credential_available == "true",
        )
        write_report(report_out, result)
    except (PrArtifactError, CommandError, OSError) as exc:
        del exc
        console.print("GitHub PR qualification artifacts could not be processed", markup=False)
        raise typer.Exit(1) from None


@github_app.command("report-pr", hidden=True)
def github_report_pr_command(
    event: Annotated[Path, typer.Option("--event")],
    context: Annotated[Path, typer.Option("--context")],
    report: Annotated[Path, typer.Option("--report")],
) -> None:
    try:
        token = _required_env("GITHUB_TOKEN")
        repository = _required_env("GITHUB_REPOSITORY")
    except CommandError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(3) from exc

    publisher = HttpxGitHubPublisher(token=token)
    try:
        context_model = read_context(context)
        report_model = read_report(report) if report.is_file() else None
        publish_pr_report(
            event,
            context_model,
            report_model,
            publisher=publisher,
            display_names=_trusted_canary_display_names(Path.cwd()),
            expected_repository=repository,
        )
    except (PrArtifactError, GitHubPublishError, OSError) as exc:
        del exc
        console.print("GitHub PR report could not be published", markup=False)
        raise typer.Exit(1) from None


if __name__ == "__main__":
    app()
