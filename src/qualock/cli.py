import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from qualock.baseline.io import BaselineStaleError
from qualock.canary.loader import CanaryLoadError
from qualock.commands import (
    BaselineUnstableError,
    CommandError,
    execute_baseline,
    execute_check,
)
from qualock.config.io import ConfigError, write_default_config
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
from qualock.qualification.models import Verdict
from qualock.report.render import render_safety_terminal, render_terminal
from qualock.report.safety import build_safety_summary
from qualock.run.docker import DockerRunner

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


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
        try:
            _config, canaries = load_project(root)
            display_names = {canary.id: canary.name for canary in canaries}
        except (ConfigError, CanaryLoadError, FileNotFoundError):
            display_names = {}
        summary = build_safety_summary(result, display_names)
        evidence_path = f".qualock/results/{result.qualification_id}/"
        console.print(render_safety_terminal(summary, evidence_path), end="", markup=False)

    if result.verdict is Verdict.BLOCK:
        raise typer.Exit(2)
    if result.verdict is Verdict.INCOMPLETE:
        raise typer.Exit(4)


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


if __name__ == "__main__":
    app()
