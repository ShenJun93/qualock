import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

import httpx

import qualock
from qualock.github_pr.models import (
    PrClassification,
    PrReportVerdict,
    PullRequestContext,
    PullRequestReport,
)

_API_BASE_URL = "https://api.github.com"
_API_VERSION = "2022-11-28"
_RESPONSE_MAX_BYTES = 2_097_152
_EVENT_MAX_BYTES = 1_048_576
_PER_PAGE = 100
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_EXPECTED_WORKFLOW_NAME = "QuaLock PR Qualification"
_EXPECTED_EVENT = "pull_request_target"
_STATUS_CONTEXT = "qualock/pr"
_BOT_LOGIN = "github-actions[bot]"
_COMMENT_MARKER = "<!-- qualock-pr-report -->"

_STATE_BY_VERDICT: dict[PrReportVerdict, str] = {
    PrReportVerdict.NOT_APPLICABLE: "success",
    PrReportVerdict.PASS: "success",
    PrReportVerdict.WARN: "failure",
    PrReportVerdict.BLOCK: "failure",
    PrReportVerdict.INCOMPLETE: "error",
}

_DESCRIPTION_BY_VERDICT: dict[PrReportVerdict, str] = {
    PrReportVerdict.NOT_APPLICABLE: "not applicable",
    PrReportVerdict.PASS: "qualification passed",
    PrReportVerdict.WARN: "qualification warning",
    PrReportVerdict.BLOCK: "qualification blocked",
    PrReportVerdict.INCOMPLETE: "qualification incomplete",
}


class GitHubPublishError(Exception):
    """Raised when a trusted publish operation cannot proceed."""


class ReporterValidationError(GitHubPublishError):
    """Raised when workflow-run identity does not bind to the producer artifacts."""


@dataclass(frozen=True)
class WorkflowRunIdentity:
    workflow_name: str
    event: str
    run_id: int
    repository_id: int
    repository_full_name: str
    trusted_head_sha: str


@dataclass(frozen=True)
class IssueComment:
    id: int
    author_login: str
    body: str


class GitHubPublisher(Protocol):
    def current_pr_head(self, repository: str, pr_number: int) -> str: ...

    def create_status(
        self,
        repository: str,
        sha: str,
        *,
        state: str,
        context: str,
        description: str,
        target_url: str,
    ) -> None: ...

    def list_issue_comments(
        self, repository: str, pr_number: int
    ) -> tuple[IssueComment, ...]: ...

    def create_issue_comment(self, repository: str, pr_number: int, body: str) -> None: ...

    def update_issue_comment(self, repository: str, comment_id: int, body: str) -> None: ...


def _read_bounded_event_text(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise GitHubPublishError(f"cannot stat workflow_run event {path}: {error}") from error
    if size > _EVENT_MAX_BYTES:
        raise GitHubPublishError(
            f"workflow_run event {path} exceeds {_EVENT_MAX_BYTES} byte limit"
        )
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise GitHubPublishError(f"cannot read workflow_run event {path}: {error}") from error


def parse_workflow_run_event(
    event_path: Path, *, expected_repository: str
) -> WorkflowRunIdentity:
    text = _read_bounded_event_text(event_path)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise GitHubPublishError(f"malformed workflow_run event JSON in {event_path}") from error
    if not isinstance(payload, dict):
        raise GitHubPublishError(f"malformed workflow_run event JSON in {event_path}")
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        raise GitHubPublishError(f"missing repository in event {event_path}")
    repository_full_name = repository.get("full_name")
    if repository_full_name != expected_repository:
        raise GitHubPublishError(
            f"event repository {repository_full_name!r} does not match "
            f"expected {expected_repository!r}"
        )
    workflow_run = payload.get("workflow_run")
    if not isinstance(workflow_run, dict):
        raise GitHubPublishError(f"missing workflow_run in event {event_path}")
    try:
        workflow_name = str(workflow_run["name"])
        event = str(workflow_run["event"])
        run_id = int(workflow_run["id"])
        repository_id = int(repository["id"])
        trusted_head_sha = str(workflow_run["head_sha"])
    except (KeyError, TypeError, ValueError) as error:
        raise GitHubPublishError(
            f"unexpected workflow_run event shape in {event_path}"
        ) from error
    if workflow_name != _EXPECTED_WORKFLOW_NAME:
        raise GitHubPublishError(f"unexpected workflow name {workflow_name!r}")
    if event != _EXPECTED_EVENT:
        raise GitHubPublishError(f"unexpected workflow_run trigger event {event!r}")
    if run_id <= 0 or repository_id <= 0:
        raise GitHubPublishError("workflow_run event has non-positive identifiers")
    if not _SHA_RE.match(trusted_head_sha):
        raise GitHubPublishError("workflow_run event has malformed head_sha")
    return WorkflowRunIdentity(
        workflow_name=workflow_name,
        event=event,
        run_id=run_id,
        repository_id=repository_id,
        repository_full_name=str(repository_full_name),
        trusted_head_sha=trusted_head_sha,
    )


def validate_reporter_inputs(
    identity: WorkflowRunIdentity,
    context: PullRequestContext,
    report: PullRequestReport | None,
) -> None:
    if identity.run_id != context.producer_run_id:
        raise ReporterValidationError("workflow run id does not match producer run id")
    if identity.repository_id != context.repository_id:
        raise ReporterValidationError("workflow run repository id does not match context")
    if identity.repository_full_name != context.repository_full_name:
        raise ReporterValidationError("workflow run repository name does not match context")
    if identity.trusted_head_sha != context.base_sha:
        raise ReporterValidationError("workflow run head sha does not match trusted base sha")
    if report is None:
        if context.classification is not PrClassification.NOT_APPLICABLE:
            raise ReporterValidationError(
                "report is required for upgrade/invalid_scope classification"
            )
        return
    if report.repository_id != context.repository_id:
        raise ReporterValidationError("report repository id does not match context")
    if report.repository_full_name != context.repository_full_name:
        raise ReporterValidationError("report repository name does not match context")
    if report.pr_number != context.pr_number:
        raise ReporterValidationError("report pr number does not match context")
    if report.base_sha != context.base_sha:
        raise ReporterValidationError("report base sha does not match context")
    if report.head_sha != context.head_sha:
        raise ReporterValidationError("report head sha does not match context")
    if report.classification != context.classification:
        raise ReporterValidationError("report classification does not match context")
    if report.qualock_version != qualock.__version__:
        raise ReporterValidationError("report qualock version does not match trusted runtime")


class HttpxGitHubPublisher:
    def __init__(self, token: str, *, client: httpx.Client | None = None) -> None:
        self._token = token
        self._client = client or httpx.Client(follow_redirects=False, timeout=15.0)

    def current_pr_head(self, repository: str, pr_number: int) -> str:
        body = self._request_bytes("GET", f"/repos/{repository}/pulls/{pr_number}", params=None)
        try:
            payload = json.loads(body)
            return str(payload["head"]["sha"])
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise GitHubPublishError(
                f"malformed pull request response for {repository}#{pr_number}"
            ) from error

    def create_status(
        self,
        repository: str,
        sha: str,
        *,
        state: str,
        context: str,
        description: str,
        target_url: str,
    ) -> None:
        self._request_bytes(
            "POST",
            f"/repos/{repository}/statuses/{sha}",
            params=None,
            json_body={
                "state": state,
                "context": context,
                "description": description,
                "target_url": target_url,
            },
        )

    def list_issue_comments(
        self, repository: str, pr_number: int
    ) -> tuple[IssueComment, ...]:
        collected: list[IssueComment] = []
        page = 1
        while True:
            body = self._request_bytes(
                "GET",
                f"/repos/{repository}/issues/{pr_number}/comments",
                params={"per_page": _PER_PAGE, "page": page},
            )
            try:
                items = json.loads(body)
                if not isinstance(items, list):
                    raise TypeError("issue comments response is not a list")
                for item in items:
                    collected.append(
                        IssueComment(
                            id=int(item["id"]),
                            author_login=str(item["user"]["login"]),
                            body=str(item["body"]),
                        )
                    )
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise GitHubPublishError(
                    f"malformed issue comments response for {repository}#{pr_number}"
                ) from error
            if len(items) < _PER_PAGE:
                break
            page += 1
        return tuple(collected)

    def create_issue_comment(self, repository: str, pr_number: int, body: str) -> None:
        self._request_bytes(
            "POST",
            f"/repos/{repository}/issues/{pr_number}/comments",
            params=None,
            json_body={"body": body},
        )

    def update_issue_comment(self, repository: str, comment_id: int, body: str) -> None:
        self._request_bytes(
            "PATCH",
            f"/repos/{repository}/issues/comments/{comment_id}",
            params=None,
            json_body={"body": body},
        )

    def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None,
        json_body: dict[str, object] | None = None,
    ) -> bytes:
        url = f"{_API_BASE_URL}{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }
        try:
            with self._client.stream(
                method, url, params=params, headers=headers, json=json_body
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise GitHubPublishError(
                        f"GitHub API returned status {response.status_code} for {path}"
                    )
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared = int(content_length)
                    except ValueError as error:
                        raise GitHubPublishError(
                            f"invalid content-length header for {path}"
                        ) from error
                    if declared > _RESPONSE_MAX_BYTES:
                        raise GitHubPublishError(
                            f"response for {path} exceeds {_RESPONSE_MAX_BYTES} byte limit"
                        )
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > _RESPONSE_MAX_BYTES:
                        raise GitHubPublishError(
                            f"response for {path} exceeds {_RESPONSE_MAX_BYTES} byte limit"
                        )
                return bytes(body)
        except httpx.TimeoutException as error:
            raise GitHubPublishError(f"timed out requesting {path}") from error
        except httpx.TransportError as error:
            raise GitHubPublishError(f"transport error requesting {path}") from error


def render_pr_comment(
    context: PullRequestContext,
    report: PullRequestReport,
    display_names: Mapping[str, str],
) -> str:
    lines = [
        _COMMENT_MARKER,
        "",
        f"## QuaLock PR Qualification: {report.verdict.value.upper()}",
        "",
    ]
    if report.classification is PrClassification.INVALID_SCOPE:
        lines.append(
            "This PR changes files outside `.qualock/baseline.lock` and cannot be qualified."
        )
    else:
        if report.baseline_version and report.candidate_version:
            lines.append(
                f"- Candidate: `{report.candidate_version}` (baseline `{report.baseline_version}`)"
            )
        if report.qualification_id:
            lines.append(f"- Qualification ID: `{report.qualification_id}`")
        if report.canaries:
            lines.append("")
            lines.append("| Canary | Verdict | Candidate | Baseline |")
            lines.append("| --- | --- | --- | --- |")
            for canary in report.canaries:
                name = display_names.get(canary.canary_id, canary.canary_id)
                lines.append(
                    f"| {name} | {canary.verdict.value} | "
                    f"{canary.candidate_successes}/{canary.candidate_valid} | "
                    f"{canary.baseline_successes}/{canary.baseline_valid} |"
                )
        if report.reason_codes:
            codes = ", ".join(code.value for code in report.reason_codes)
            lines.append("")
            lines.append(f"Reason codes: {codes}")
    return "\n".join(lines) + "\n"


def publish_pr_report(
    event_path: Path,
    context: PullRequestContext,
    report: PullRequestReport | None,
    *,
    publisher: GitHubPublisher,
    display_names: Mapping[str, str],
    expected_repository: str,
) -> None:
    identity = parse_workflow_run_event(event_path, expected_repository=expected_repository)
    validate_reporter_inputs(identity, context, report)

    artifact_sha = report.head_sha if report is not None else context.head_sha
    verdict = report.verdict if report is not None else PrReportVerdict.NOT_APPLICABLE
    target_url = (
        f"https://github.com/{identity.repository_full_name}"
        f"/actions/runs/{context.producer_run_id}"
    )
    publisher.create_status(
        context.repository_full_name,
        artifact_sha,
        state=_STATE_BY_VERDICT[verdict],
        context=_STATUS_CONTEXT,
        description=_DESCRIPTION_BY_VERDICT[verdict],
        target_url=target_url,
    )

    if report is None or verdict is PrReportVerdict.NOT_APPLICABLE:
        return

    current_head = publisher.current_pr_head(context.repository_full_name, context.pr_number)
    if current_head != artifact_sha:
        return

    body = render_pr_comment(context, report, display_names)
    existing = publisher.list_issue_comments(context.repository_full_name, context.pr_number)
    markers = [
        comment
        for comment in existing
        if comment.author_login == _BOT_LOGIN and _COMMENT_MARKER in comment.body
    ]
    if markers:
        publisher.update_issue_comment(context.repository_full_name, markers[-1].id, body)
    else:
        publisher.create_issue_comment(context.repository_full_name, context.pr_number, body)
