import json
import os
import re
import textwrap

import pytest
import yaml

from qualock.github_pr.templates import PRODUCER_WORKFLOW, REPORTER_WORKFLOW

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

MODEL_SECRET_NAMES = (
    "QUALOCK_CODEX_AUTH_B64",
    "QUALOCK_ANTHROPIC_AUTH_TOKEN",
    "QUALOCK_ANTHROPIC_API_KEY",
    "QUALOCK_CLAUDE_CODE_OAUTH_TOKEN",
)

FORBIDDEN_SUBSTRINGS = (
    "github.event.pull_request.head.sha }}\n          path:",
    "refs/pull/",
    "gh pr checkout",
    "git fetch",
    "pull_request.head.repo",
)


def parsed(workflow: str) -> object:
    return yaml.load(workflow, Loader=yaml.BaseLoader)


def action_refs(node: object) -> list[str]:
    refs: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses" and isinstance(value, str):
                refs.append(value)
            refs.extend(action_refs(value))
    elif isinstance(node, list):
        for value in node:
            refs.extend(action_refs(value))
    return refs


def test_every_action_is_pinned_to_a_full_sha() -> None:
    for workflow in (PRODUCER_WORKFLOW, REPORTER_WORKFLOW):
        for ref in action_refs(parsed(workflow)):
            assert FULL_SHA.fullmatch(ref.rsplit("@", 1)[1])


def test_workflows_reject_forbidden_substrings() -> None:
    for workflow in (PRODUCER_WORKFLOW, REPORTER_WORKFLOW):
        for forbidden in FORBIDDEN_SUBSTRINGS:
            assert forbidden not in workflow


def test_producer_name_is_exact() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    assert doc["name"] == "QuaLock PR Qualification"


def test_producer_trigger_activity_types_are_exact() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    trigger = doc["on"]["pull_request_target"]
    assert trigger["types"] == ["opened", "reopened", "synchronize", "ready_for_review"]


def test_producer_permissions_are_exact() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    assert doc["permissions"] == {"contents": "read", "pull-requests": "read"}


def test_producer_concurrency_group_and_cancel() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    concurrency = doc["concurrency"]
    assert "github.repository" in concurrency["group"]
    assert "github.event.pull_request.number" in concurrency["group"]
    assert concurrency["cancel-in-progress"] == "true"


def _steps(doc: dict[str, object]) -> list[dict[str, object]]:
    jobs = doc["jobs"]
    assert isinstance(jobs, dict)
    job = next(iter(jobs.values()))
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    return steps


def test_producer_checkout_ref_and_persist_credentials() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    checkout = next(
        step for step in _steps(doc) if str(step.get("uses", "")).startswith("actions/checkout")
    )
    checkout_with = checkout["with"]
    assert checkout_with["ref"] == "${{ github.event.pull_request.base.sha }}"
    assert checkout_with["persist-credentials"] == "false"


def test_producer_prepare_pr_runs_before_credential_reference() -> None:
    prepare_index = PRODUCER_WORKFLOW.index("qualock github prepare-pr")
    credential_index = PRODUCER_WORKFLOW.index("QUALOCK_CODEX_AUTH_B64")
    assert prepare_index < credential_index


def test_producer_context_artifact_name() -> None:
    assert "qualock-pr-context" in PRODUCER_WORKFLOW


def test_producer_prepare_pr_writes_proposed_lock_consumed_by_qualify_pr() -> None:
    assert (
        '--proposed-lock-out "$RUNNER_TEMP/proposed-baseline.lock"' in PRODUCER_WORKFLOW
    )


def test_producer_report_artifact_upload_always() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    upload_steps = [
        step
        for step in _steps(doc)
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    ]
    report_upload = next(
        step for step in upload_steps if step["with"]["name"] == "qualock-pr-report"
    )
    assert report_upload["if"] == "always()"


def test_producer_auth_cleanup_always() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    cleanup_step = next(
        step
        for step in _steps(doc)
        if isinstance(step.get("run"), str)
        and ".codex/auth.json" in step["run"]
        and "rm" in step["run"]
    )
    assert cleanup_step["if"] == "always()"


def test_reporter_name_is_exact() -> None:
    doc = parsed(REPORTER_WORKFLOW)
    assert isinstance(doc, dict)
    assert doc["name"] == "QuaLock PR Reporter"


def test_reporter_trigger_is_exact() -> None:
    doc = parsed(REPORTER_WORKFLOW)
    assert isinstance(doc, dict)
    trigger = doc["on"]["workflow_run"]
    assert trigger["workflows"] == ["QuaLock PR Qualification"]
    assert trigger["types"] == ["completed"]


def test_reporter_permissions_are_exact() -> None:
    doc = parsed(REPORTER_WORKFLOW)
    assert isinstance(doc, dict)
    assert doc["permissions"] == {
        "actions": "read",
        "contents": "read",
        "statuses": "write",
        "pull-requests": "write",
    }


def test_reporter_checkout_ref_and_persist_credentials() -> None:
    doc = parsed(REPORTER_WORKFLOW)
    assert isinstance(doc, dict)
    checkout = next(
        step for step in _steps(doc) if str(step.get("uses", "")).startswith("actions/checkout")
    )
    checkout_with = checkout["with"]
    assert checkout_with["ref"] == "${{ github.event.workflow_run.head_sha }}"
    assert checkout_with["persist-credentials"] == "false"


def test_reporter_downloads_use_triggering_run_and_runner_temp() -> None:
    doc = parsed(REPORTER_WORKFLOW)
    assert isinstance(doc, dict)
    download_steps = [
        step
        for step in _steps(doc)
        if str(step.get("uses", "")).startswith("actions/download-artifact")
    ]
    assert len(download_steps) == 2
    for step in download_steps:
        assert step["with"]["run-id"] == "${{ github.event.workflow_run.id }}"
        assert str(step["with"]["path"]).startswith("${{ runner.temp }}")


def test_reporter_report_download_allowed_to_fail() -> None:
    doc = parsed(REPORTER_WORKFLOW)
    assert isinstance(doc, dict)
    download_steps = [
        step
        for step in _steps(doc)
        if str(step.get("uses", "")).startswith("actions/download-artifact")
    ]
    report_download = next(
        step for step in download_steps if step["with"]["name"] == "qualock-pr-report"
    )
    assert report_download["continue-on-error"] == "true"


def test_reporter_has_no_credential_reference() -> None:
    assert "QUALOCK_CODEX_AUTH_B64" not in REPORTER_WORKFLOW


def test_producer_credential_directory_created_with_restrictive_mode() -> None:
    assert 'install -d -m 700 "$HOME/.codex"' in PRODUCER_WORKFLOW


def test_producer_credential_file_chmod_restrictive_after_decode() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    credential_step = next(
        step
        for step in _steps(doc)
        if isinstance(step.get("run"), str) and "QUALOCK_CODEX_AUTH_B64" in step["run"]
    )
    run_script = credential_step["run"]
    decode_index = run_script.index("base64 -d")
    chmod_index = run_script.index('chmod 600 "$HOME/.codex/auth.json"')
    assert chmod_index > decode_index


def _plan_step(doc: dict[str, object]) -> dict[str, object]:
    return next(step for step in _steps(doc) if step.get("id") == "plan")


def _named_step(doc: dict[str, object], name: str) -> dict[str, object]:
    return next(step for step in _steps(doc) if step.get("name") == name)


def _plan_script(doc: dict[str, object]) -> str:
    run = _plan_step(doc)["run"]
    assert isinstance(run, str)
    lines = run.splitlines()
    start = next(i for i, line in enumerate(lines) if "<<'PY'" in line) + 1
    end = next(i for i, line in enumerate(lines) if line.strip() == "PY")
    return textwrap.dedent("\n".join(lines[start:end]))


def _run_plan_script(
    script: str,
    *,
    classification: str,
    agent: str | None,
    lock_exists: bool,
    report_exists: bool,
    tmp_path,
) -> dict[str, str]:
    context = {"classification": classification}
    if agent is not None:
        context["agent"] = agent
    context_path = tmp_path / "pr-context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")

    lock_path = tmp_path / "proposed-baseline.lock"
    if lock_exists:
        lock_path.write_text("{}", encoding="utf-8")

    report_path = tmp_path / "pr-report.json"
    if report_exists:
        report_path.write_text("{}", encoding="utf-8")

    output_path = tmp_path / "github-output"
    output_path.write_text("", encoding="utf-8")

    env_overrides = {
        "QUALOCK_CONTEXT": str(context_path),
        "QUALOCK_PROPOSED_LOCK": str(lock_path),
        "QUALOCK_REPORT": str(report_path),
        "GITHUB_OUTPUT": str(output_path),
    }
    saved = dict(os.environ)
    try:
        os.environ.update(env_overrides)
        exec(compile(script, "<plan-step>", "exec"), {})  # noqa: S102
    finally:
        os.environ.clear()
        os.environ.update(saved)

    lines = output_path.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines)


def test_producer_plan_step_precedes_every_model_secret_reference() -> None:
    prepare_index = PRODUCER_WORKFLOW.index("qualock github prepare-pr")
    plan_index = PRODUCER_WORKFLOW.index("id: plan")
    assert prepare_index < plan_index
    for name in MODEL_SECRET_NAMES:
        assert plan_index < PRODUCER_WORKFLOW.index(name)


def test_producer_plan_step_reads_only_fixed_context_report_and_lock_paths() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    env = _plan_step(doc)["env"]
    assert env == {
        "QUALOCK_CONTEXT": "${{ runner.temp }}/pr-context.json",
        "QUALOCK_PROPOSED_LOCK": "${{ runner.temp }}/proposed-baseline.lock",
        "QUALOCK_REPORT": "${{ runner.temp }}/pr-report.json",
    }


def test_producer_plan_step_emits_exactly_bounded_output_keys() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    script = _plan_script(doc)
    compile(script, "<plan-step>", "exec")
    assert script.count("output.write(") == 3
    for key in ("classification", "agent", "ready"):
        assert f"{key}=" in script


@pytest.mark.parametrize(
    ("classification", "agent", "lock_exists", "report_exists", "expected_ready"),
    [
        ("upgrade", "codex", True, False, True),
        ("upgrade", "claude", True, False, True),
        ("not_applicable", "codex", True, False, False),
        ("invalid_scope", "codex", True, False, False),
        ("upgrade", "unsupported-agent", True, False, False),
        ("upgrade", None, True, False, False),
        ("upgrade", "codex", False, False, False),
        ("upgrade", "codex", True, True, False),
    ],
)
def test_producer_plan_step_ready_matches_reference_formula(
    tmp_path, classification: str, agent: str | None, lock_exists: bool,
    report_exists: bool, expected_ready: bool,
) -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    script = _plan_script(doc)
    outputs = _run_plan_script(
        script,
        classification=classification,
        agent=agent,
        lock_exists=lock_exists,
        report_exists=report_exists,
        tmp_path=tmp_path,
    )
    assert set(outputs) == {"classification", "agent", "ready"}
    assert outputs["classification"] == classification
    assert outputs["agent"] == (agent or "")
    assert outputs["ready"] == ("true" if expected_ready else "false")


def test_producer_codex_credential_condition_requires_plan_ready_and_codex() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    credential_step = next(step for step in _steps(doc) if step.get("id") == "credential")
    assert (
        credential_step["if"]
        == "steps.plan.outputs.ready == 'true' && steps.plan.outputs.agent == 'codex'"
    )


def test_producer_codex_qualify_condition_requires_plan_ready_and_codex() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    step = _named_step(doc, "Qualify upgrade (codex)")
    assert (
        step["if"] == "steps.plan.outputs.ready == 'true' && steps.plan.outputs.agent == 'codex'"
    )


def test_producer_claude_qualify_condition_requires_plan_ready_and_claude() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    step = _named_step(doc, "Qualify upgrade (claude)")
    assert (
        step["if"] == "steps.plan.outputs.ready == 'true' && steps.plan.outputs.agent == 'claude'"
    )


def test_producer_codex_and_claude_qualification_conditions_are_mutually_exclusive() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    codex_if = _named_step(doc, "Qualify upgrade (codex)")["if"]
    claude_if = _named_step(doc, "Qualify upgrade (claude)")["if"]
    assert codex_if != claude_if
    assert "agent == 'codex'" in codex_if
    assert "agent == 'claude'" in claude_if
    assert "agent == 'claude'" not in codex_if
    assert "agent == 'codex'" not in claude_if


def test_producer_unsupported_or_null_agent_enters_neither_secret_bearing_path() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    credential_if = next(step for step in _steps(doc) if step.get("id") == "credential")["if"]
    codex_if = _named_step(doc, "Qualify upgrade (codex)")["if"]
    claude_if = _named_step(doc, "Qualify upgrade (claude)")["if"]
    for condition in (credential_if, codex_if, claude_if):
        assert "steps.plan.outputs.ready == 'true'" in condition
        assert "steps.plan.outputs.agent ==" in condition


def _step_secret_bearing_text(step: dict[str, object]) -> str:
    parts: list[str] = []
    run = step.get("run")
    if isinstance(run, str):
        parts.append(run)
    env = step.get("env")
    if isinstance(env, dict):
        parts.extend(f"{key}={value}" for key, value in env.items())
    return "\n".join(parts)


@pytest.mark.parametrize(
    ("secret_name", "step_name"),
    [
        ("QUALOCK_CODEX_AUTH_B64", "Materialize codex credential"),
        ("QUALOCK_ANTHROPIC_AUTH_TOKEN", "Qualify upgrade (claude)"),
        ("QUALOCK_ANTHROPIC_API_KEY", "Qualify upgrade (claude)"),
        ("QUALOCK_CLAUDE_CODE_OAUTH_TOKEN", "Qualify upgrade (claude)"),
    ],
)
def test_producer_model_secret_is_isolated_to_a_single_step(
    secret_name: str, step_name: str
) -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    target_step = _named_step(doc, step_name)
    isolated_count = _step_secret_bearing_text(target_step).count(secret_name)
    assert isolated_count > 0
    assert PRODUCER_WORKFLOW.count(secret_name) == isolated_count


def test_reporter_workflow_contains_no_model_secret_names() -> None:
    for name in MODEL_SECRET_NAMES:
        assert name not in REPORTER_WORKFLOW


def test_producer_claude_step_never_writes_secret_to_github_output() -> None:
    doc = parsed(PRODUCER_WORKFLOW)
    assert isinstance(doc, dict)
    step = _named_step(doc, "Qualify upgrade (claude)")
    run_script = step["run"]
    assert isinstance(run_script, str)
    assert "GITHUB_OUTPUT" not in run_script
    assert "set +x" in run_script
