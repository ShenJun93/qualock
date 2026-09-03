import re

import yaml

from qualock.github_pr.templates import PRODUCER_WORKFLOW, REPORTER_WORKFLOW

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

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
