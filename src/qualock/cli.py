from pathlib import Path
import shutil

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
from qualock.config.io import ConfigError, load_config, write_default_config
from qualock.project import load_project, project_dir
from qualock.project_protection.commands import (
    ProjectProtectionConfigError,
    execute_protect as execute_project_protect,
    execute_verify as execute_project_verify,
)
from qualock.project_protection.render import render_protect_terminal, render_verify_terminal
from qualock.project_protection.models import ProtectionStatus
from qualock.project_protection.runner import ProjectProtectionError
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


@app.command("protect")
def protect_command() -> None:
    root = Path.cwd()
    try:
        result = execute_project_protect(root)
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
