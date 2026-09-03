import json
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

import qualock
from qualock.github_pr.models import PrClassification, PrReportVerdict, PullRequestContext, PullRequestReport
from qualock.github_pr.publisher import (
    GitHubPublishError,
    GitHubPublisher,
    HttpxGitHubPublisher,
    IssueComment,
    ReporterValidationError,
    WorkflowRunIdentity,
    parse_workflow_run_event,
    publish_pr_report,
    render_pr_comment,
    validate_reporter_inputs,
)

DEFAULT_REPOSITORY = "owner/repo"
DEFAULT_REPOSITORY_ID = 123
DEFAULT_PR_NUMBER = 7
DEFAULT_RUN_ID = 999


def context_fixture(
    *,
    classification: PrClassification = PrClassification.UPGRADE,
    repository: str = DEFAULT_REPOSITORY,
    repository_id: int = DEFAULT_REPOSITORY_ID,
    pr_number: int = DEFAULT_PR_NUMBER,
    base_sha: str = "a" * 40,
    head_sha: str = "b" * 40,
    producer_run_id: int = DEFAULT_RUN_ID,
) -> PullRequestContext:
    return PullRequestContext(
        repository_id=repository_id,
        repository_full_name=repository,
        pr_number=pr_number,
        pr_author_login="author",
        base_sha=base_sha,
        head_sha=head_sha,
        producer_run_id=producer_run_id,
        changed_paths=(".qualock/baseline.lock",),
        classification=classification,
    )


def report_fixture(
    context: PullRequestContext, *, verdict: PrReportVerdict = PrReportVerdict.PASS
) -> PullRequestReport:
    return PullRequestReport(
        repository_id=context.repository_id,
        repository_full_name=context.repository_full_name,
        pr_number=context.pr_number,
        base_sha=context.base_sha,
        head_sha=context.head_sha,
        producer_run_id=context.producer_run_id,
        classification=context.classification,
        baseline_version="1.0.0",
        candidate_version="1.1.0",
        qualification_id="qual-1",
        qualock_version=qualock.__version__,
        verdict=verdict,
        credential_unavailable=False,
        qualification_completed=True,
    )


def workflow_run_fixture(
    tmp_path: Path,
    context: PullRequestContext,
    *,
    workflow_name: str = "QuaLock PR Qualification",
    event: str = "pull_request_target",
    run_id: int | None = None,
    repository_id: int | None = None,
    repository: str | None = None,
    head_sha: str | None = None,
    filename: str = "workflow_run_event.json",
) -> Path:
    event_path = tmp_path / filename
    event_path.write_text(
        json.dumps(
            {
                "action": "completed",
                "workflow_run": {
                    "name": workflow_name,
                    "event": event,
                    "id": run_id if run_id is not None else context.producer_run_id,
                    "head_sha": head_sha if head_sha is not None else context.base_sha,
                },
                "repository": {
                    "id": repository_id if repository_id is not None else context.repository_id,
                    "full_name": repository if repository is not None else context.repository_full_name,
                },
            }
        ),
        encoding="utf-8",
    )
    return event_path


def identity_fixture(context: PullRequestContext) -> WorkflowRunIdentity:
    return WorkflowRunIdentity(
        workflow_name="QuaLock PR Qualification",
        event="pull_request_target",
        run_id=context.producer_run_id,
        repository_id=context.repository_id,
        repository_full_name=context.repository_full_name,
        trusted_head_sha=context.base_sha,
    )


# --- parse_workflow_run_event ---


def test_parse_valid_event_returns_identity(tmp_path: Path) -> None:
    context = context_fixture()
    event_path = workflow_run_fixture(tmp_path, context)
    identity = parse_workflow_run_event(event_path, expected_repository=DEFAULT_REPOSITORY)
    assert identity == identity_fixture(context)


def test_parse_rejects_wrong_workflow_name(tmp_path: Path) -> None:
    context = context_fixture()
    event_path = workflow_run_fixture(tmp_path, context, workflow_name="Some Other Workflow")
    with pytest.raises(GitHubPublishError):
        parse_workflow_run_event(event_path, expected_repository=DEFAULT_REPOSITORY)


def test_parse_rejects_wrong_event(tmp_path: Path) -> None:
    context = context_fixture()
    event_path = workflow_run_fixture(tmp_path, context, event="pull_request")
    with pytest.raises(GitHubPublishError):
        parse_workflow_run_event(event_path, expected_repository=DEFAULT_REPOSITORY)


def test_parse_rejects_expected_repository_mismatch(tmp_path: Path) -> None:
    context = context_fixture()
    event_path = workflow_run_fixture(tmp_path, context, repository="owner/other")
    with pytest.raises(GitHubPublishError):
        parse_workflow_run_event(event_path, expected_repository=DEFAULT_REPOSITORY)


def test_parse_rejects_non_positive_run_id(tmp_path: Path) -> None:
    context = context_fixture()
    event_path = workflow_run_fixture(tmp_path, context, run_id=0)
    with pytest.raises(GitHubPublishError):
        parse_workflow_run_event(event_path, expected_repository=DEFAULT_REPOSITORY)


def test_parse_rejects_non_positive_repository_id(tmp_path: Path) -> None:
    context = context_fixture()
    event_path = workflow_run_fixture(tmp_path, context, repository_id=0)
    with pytest.raises(GitHubPublishError):
        parse_workflow_run_event(event_path, expected_repository=DEFAULT_REPOSITORY)


def test_parse_rejects_malformed_head_sha(tmp_path: Path) -> None:
    context = context_fixture()
    event_path = workflow_run_fixture(tmp_path, context, head_sha="not-a-sha")
    with pytest.raises(GitHubPublishError):
        parse_workflow_run_event(event_path, expected_repository=DEFAULT_REPOSITORY)


def test_parse_rejects_malformed_json(tmp_path: Path) -> None:
    event_path = tmp_path / "bad.json"
    event_path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(GitHubPublishError):
        parse_workflow_run_event(event_path, expected_repository=DEFAULT_REPOSITORY)


def test_parse_rejects_missing_workflow_run_key(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"repository": {"id": DEFAULT_REPOSITORY_ID, "full_name": DEFAULT_REPOSITORY}}),
        encoding="utf-8",
    )
    with pytest.raises(GitHubPublishError):
        parse_workflow_run_event(event_path, expected_repository=DEFAULT_REPOSITORY)


def test_parse_rejects_oversize_event(tmp_path: Path) -> None:
    event_path = tmp_path / "big.json"
    padding = "x" * (1_048_576 + 1)
    event_path.write_text(json.dumps({"padding": padding}), encoding="utf-8")
    with pytest.raises(GitHubPublishError):
        parse_workflow_run_event(event_path, expected_repository=DEFAULT_REPOSITORY)


# --- validate_reporter_inputs ---


def test_validate_rejects_run_id_mismatch() -> None:
    context = context_fixture()
    identity = WorkflowRunIdentity(
        workflow_name="QuaLock PR Qualification",
        event="pull_request_target",
        run_id=context.producer_run_id + 1,
        repository_id=context.repository_id,
        repository_full_name=context.repository_full_name,
        trusted_head_sha=context.base_sha,
    )
    with pytest.raises(ReporterValidationError):
        validate_reporter_inputs(identity, context, report_fixture(context))


def test_validate_rejects_repository_id_mismatch() -> None:
    context = context_fixture()
    identity = WorkflowRunIdentity(
        workflow_name="QuaLock PR Qualification",
        event="pull_request_target",
        run_id=context.producer_run_id,
        repository_id=context.repository_id + 1,
        repository_full_name=context.repository_full_name,
        trusted_head_sha=context.base_sha,
    )
    with pytest.raises(ReporterValidationError):
        validate_reporter_inputs(identity, context, report_fixture(context))


def test_validate_rejects_repository_full_name_mismatch() -> None:
    context = context_fixture()
    identity = WorkflowRunIdentity(
        workflow_name="QuaLock PR Qualification",
        event="pull_request_target",
        run_id=context.producer_run_id,
        repository_id=context.repository_id,
        repository_full_name="owner/other",
        trusted_head_sha=context.base_sha,
    )
    with pytest.raises(ReporterValidationError):
        validate_reporter_inputs(identity, context, report_fixture(context))


def test_validate_rejects_trusted_head_sha_mismatch() -> None:
    context = context_fixture()
    identity = WorkflowRunIdentity(
        workflow_name="QuaLock PR Qualification",
        event="pull_request_target",
        run_id=context.producer_run_id,
        repository_id=context.repository_id,
        repository_full_name=context.repository_full_name,
        trusted_head_sha="c" * 40,
    )
    with pytest.raises(ReporterValidationError):
        validate_reporter_inputs(identity, context, report_fixture(context))


def test_validate_rejects_pr_number_mismatch() -> None:
    context = context_fixture()
    report = report_fixture(context).model_copy(update={"pr_number": context.pr_number + 1})
    with pytest.raises(ReporterValidationError):
        validate_reporter_inputs(identity_fixture(context), context, report)


def test_validate_rejects_base_sha_mismatch() -> None:
    context = context_fixture()
    report = report_fixture(context).model_copy(update={"base_sha": "d" * 40})
    with pytest.raises(ReporterValidationError):
        validate_reporter_inputs(identity_fixture(context), context, report)


def test_validate_rejects_head_sha_mismatch() -> None:
    context = context_fixture()
    report = report_fixture(context).model_copy(update={"head_sha": "e" * 40})
    with pytest.raises(ReporterValidationError):
        validate_reporter_inputs(identity_fixture(context), context, report)


def test_validate_rejects_classification_mismatch() -> None:
    context = context_fixture()
    report = report_fixture(context).model_copy(
        update={"classification": PrClassification.INVALID_SCOPE}
    )
    with pytest.raises(ReporterValidationError):
        validate_reporter_inputs(identity_fixture(context), context, report)


def test_validate_rejects_qualock_version_mismatch() -> None:
    context = context_fixture()
    report = report_fixture(context).model_copy(update={"qualock_version": "0.0.0"})
    with pytest.raises(ReporterValidationError):
        validate_reporter_inputs(identity_fixture(context), context, report)


def test_validate_allows_not_applicable_without_report() -> None:
    context = context_fixture(classification=PrClassification.NOT_APPLICABLE)
    validate_reporter_inputs(identity_fixture(context), context, None)


def test_validate_rejects_missing_report_for_upgrade() -> None:
    context = context_fixture(classification=PrClassification.UPGRADE)
    with pytest.raises(ReporterValidationError):
        validate_reporter_inputs(identity_fixture(context), context, None)


def test_validate_rejects_missing_report_for_invalid_scope() -> None:
    context = context_fixture(classification=PrClassification.INVALID_SCOPE)
    with pytest.raises(ReporterValidationError):
        validate_reporter_inputs(identity_fixture(context), context, None)


def test_validate_accepts_matching_identity_context_report() -> None:
    context = context_fixture()
    validate_reporter_inputs(identity_fixture(context), context, report_fixture(context))


# --- HttpxGitHubPublisher narrow HTTP behavior ---


def mock_publisher_client(
    *,
    head_sha: str = "b" * 40,
    comments: list[dict[str, object]] | None = None,
    status_recorder: list[httpx.Request] | None = None,
    pr_status_code: int = 200,
    comments_status_code: int = 200,
    oversize_comments: bool = False,
) -> httpx.Client:
    comments = comments or []

    def handler(request: httpx.Request) -> httpx.Response:
        if status_recorder is not None:
            status_recorder.append(request)
        path = request.url.path
        if path.startswith("/repos/") and "/pulls/" in path and request.method == "GET":
            if pr_status_code != 200:
                return httpx.Response(pr_status_code, json={"message": "error"})
            return httpx.Response(200, json={"head": {"sha": head_sha}})
        if path.endswith("/comments") and "/issues/" in path and request.method == "GET":
            if comments_status_code != 200:
                return httpx.Response(comments_status_code, json={"message": "error"})
            if oversize_comments:
                return httpx.Response(
                    200,
                    content=json.dumps([{"padding": "x" * (2_097_152 + 1)}]).encode("utf-8"),
                )
            page = int(request.url.params.get("page", "1"))
            if page == 1:
                return httpx.Response(200, json=comments)
            return httpx.Response(200, json=[])
        if "/statuses/" in path and request.method == "POST":
            return httpx.Response(201, json={"id": 1})
        if path.endswith("/comments") and "/issues/" in path and request.method == "POST":
            return httpx.Response(201, json={"id": 42})
        if "/issues/comments/" in path and request.method == "PATCH":
            return httpx.Response(200, json={"id": 5})
        return httpx.Response(404, json={"message": "not found"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_current_pr_head_parses_head_sha() -> None:
    client = mock_publisher_client(head_sha="f" * 40)
    publisher = HttpxGitHubPublisher(token="test-token", client=client)
    assert publisher.current_pr_head(DEFAULT_REPOSITORY, DEFAULT_PR_NUMBER) == "f" * 40


def test_current_pr_head_rejects_non_2xx() -> None:
    client = mock_publisher_client(pr_status_code=500)
    publisher = HttpxGitHubPublisher(token="test-token", client=client)
    with pytest.raises(GitHubPublishError):
        publisher.current_pr_head(DEFAULT_REPOSITORY, DEFAULT_PR_NUMBER)


def test_create_status_sends_expected_payload() -> None:
    requests_log: list[httpx.Request] = []
    client = mock_publisher_client(status_recorder=requests_log)
    publisher = HttpxGitHubPublisher(token="test-token", client=client)
    publisher.create_status(
        DEFAULT_REPOSITORY,
        "b" * 40,
        state="success",
        context="qualock/pr",
        description="qualification passed",
        target_url="https://github.com/owner/repo/actions/runs/999",
    )
    status_requests = [r for r in requests_log if "/statuses/" in r.url.path]
    assert len(status_requests) == 1
    payload = json.loads(status_requests[0].content)
    assert payload == {
        "state": "success",
        "context": "qualock/pr",
        "description": "qualification passed",
        "target_url": "https://github.com/owner/repo/actions/runs/999",
    }


def test_list_issue_comments_paginates() -> None:
    comments = [{"id": i, "user": {"login": "github-actions[bot]"}, "body": f"c{i}"} for i in range(3)]
    client = mock_publisher_client(comments=comments)
    publisher = HttpxGitHubPublisher(token="test-token", client=client)
    result = publisher.list_issue_comments(DEFAULT_REPOSITORY, DEFAULT_PR_NUMBER)
    assert result == tuple(
        IssueComment(id=i, author_login="github-actions[bot]", body=f"c{i}") for i in range(3)
    )


def test_list_issue_comments_rejects_non_2xx() -> None:
    client = mock_publisher_client(comments_status_code=500)
    publisher = HttpxGitHubPublisher(token="test-token", client=client)
    with pytest.raises(GitHubPublishError):
        publisher.list_issue_comments(DEFAULT_REPOSITORY, DEFAULT_PR_NUMBER)


def test_response_over_size_cap_is_rejected() -> None:
    client = mock_publisher_client(oversize_comments=True)
    publisher = HttpxGitHubPublisher(token="test-token", client=client)
    with pytest.raises(GitHubPublishError):
        publisher.list_issue_comments(DEFAULT_REPOSITORY, DEFAULT_PR_NUMBER)


def test_redirect_response_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://evil.example/pwn"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    publisher = HttpxGitHubPublisher(token="test-token", client=client)
    with pytest.raises(GitHubPublishError):
        publisher.current_pr_head(DEFAULT_REPOSITORY, DEFAULT_PR_NUMBER)


def test_create_issue_comment_and_update_issue_comment_succeed() -> None:
    client = mock_publisher_client()
    publisher = HttpxGitHubPublisher(token="test-token", client=client)
    publisher.create_issue_comment(DEFAULT_REPOSITORY, DEFAULT_PR_NUMBER, "hello")
    publisher.update_issue_comment(DEFAULT_REPOSITORY, 42, "hello again")


def test_production_client_uses_no_redirects_and_bounded_timeout() -> None:
    publisher = HttpxGitHubPublisher(token="test-token")
    assert publisher._client.follow_redirects is False
    assert publisher._client.timeout.connect == 15.0


# --- publish_pr_report / render_pr_comment (Cycle B) ---


@dataclass
class RecordedStatus:
    repository: str
    sha: str
    state: str
    context: str
    description: str
    target_url: str


class RecordingPublisher:
    def __init__(
        self,
        *,
        current_head: str,
        comments: list[IssueComment] | None = None,
    ) -> None:
        self.current_head = current_head
        self.statuses: list[RecordedStatus] = []
        self.comments: list[IssueComment] = comments or []
        self._next_comment_id = max((c.id for c in self.comments), default=0) + 1

    def current_pr_head(self, repository: str, pr_number: int) -> str:
        return self.current_head

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
        self.statuses.append(
            RecordedStatus(repository, sha, state, context, description, target_url)
        )

    def list_issue_comments(
        self, repository: str, pr_number: int
    ) -> tuple[IssueComment, ...]:
        return tuple(self.comments)

    def create_issue_comment(self, repository: str, pr_number: int, body: str) -> None:
        comment = IssueComment(
            id=self._next_comment_id, author_login="github-actions[bot]", body=body
        )
        self._next_comment_id += 1
        self.comments.append(comment)

    def update_issue_comment(self, repository: str, comment_id: int, body: str) -> None:
        for index, comment in enumerate(self.comments):
            if comment.id == comment_id:
                self.comments[index] = IssueComment(
                    id=comment.id, author_login=comment.author_login, body=body
                )
                return
        raise AssertionError(f"comment {comment_id} does not exist")


class FailingPublisher(RecordingPublisher):
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
        raise GitHubPublishError("simulated status write failure")


@pytest.mark.parametrize(
    ("verdict", "state"),
    [
        (PrReportVerdict.NOT_APPLICABLE, "success"),
        (PrReportVerdict.PASS, "success"),
        (PrReportVerdict.WARN, "failure"),
        (PrReportVerdict.BLOCK, "failure"),
        (PrReportVerdict.INCOMPLETE, "error"),
    ],
)
def test_status_mapping_is_exact(
    tmp_path: Path, verdict: PrReportVerdict, state: str
) -> None:
    classification = (
        PrClassification.NOT_APPLICABLE
        if verdict is PrReportVerdict.NOT_APPLICABLE
        else PrClassification.UPGRADE
    )
    context = context_fixture(classification=classification)
    report = report_fixture(context, verdict=verdict)
    publisher = RecordingPublisher(current_head=report.head_sha)
    event_path = workflow_run_fixture(tmp_path, context)
    publish_pr_report(
        event_path,
        context,
        report,
        publisher=publisher,
        display_names={},
        expected_repository=DEFAULT_REPOSITORY,
    )
    assert publisher.statuses[-1].state == state
    assert publisher.statuses[-1].context == "qualock/pr"


def test_status_target_url_from_validated_identity(tmp_path: Path) -> None:
    context = context_fixture()
    report = report_fixture(context)
    publisher = RecordingPublisher(current_head=report.head_sha)
    event_path = workflow_run_fixture(tmp_path, context)
    publish_pr_report(
        event_path,
        context,
        report,
        publisher=publisher,
        display_names={},
        expected_repository=DEFAULT_REPOSITORY,
    )
    assert publisher.statuses[-1].target_url == (
        f"https://github.com/{context.repository_full_name}/actions/runs/{context.producer_run_id}"
    )


def test_status_written_to_artifact_head_even_when_stale(tmp_path: Path) -> None:
    context = context_fixture()
    report = report_fixture(context)
    publisher = RecordingPublisher(current_head="f" * 40)
    event_path = workflow_run_fixture(tmp_path, context)
    publish_pr_report(
        event_path,
        context,
        report,
        publisher=publisher,
        display_names={},
        expected_repository=DEFAULT_REPOSITORY,
    )
    assert publisher.statuses[-1].sha == report.head_sha


def test_stale_run_never_creates_or_updates_comment(tmp_path: Path) -> None:
    context = context_fixture()
    report = report_fixture(context)
    publisher = RecordingPublisher(current_head="f" * 40)
    event_path = workflow_run_fixture(tmp_path, context)
    publish_pr_report(
        event_path,
        context,
        report,
        publisher=publisher,
        display_names={},
        expected_repository=DEFAULT_REPOSITORY,
    )
    assert publisher.comments == []


def test_current_head_upgrade_creates_one_marker_comment(tmp_path: Path) -> None:
    context = context_fixture()
    report = report_fixture(context)
    publisher = RecordingPublisher(current_head=report.head_sha)
    event_path = workflow_run_fixture(tmp_path, context)
    publish_pr_report(
        event_path,
        context,
        report,
        publisher=publisher,
        display_names={},
        expected_repository=DEFAULT_REPOSITORY,
    )
    assert len(publisher.comments) == 1
    assert publisher.comments[0].author_login == "github-actions[bot]"
    assert "<!-- qualock-pr-report -->" in publisher.comments[0].body


def test_rerun_updates_newest_bot_marker_comment(tmp_path: Path) -> None:
    context = context_fixture()
    report = report_fixture(context)
    existing = [
        IssueComment(
            id=1, author_login="github-actions[bot]", body="<!-- qualock-pr-report -->\nold-1"
        ),
        IssueComment(
            id=2, author_login="github-actions[bot]", body="<!-- qualock-pr-report -->\nold-2"
        ),
    ]
    publisher = RecordingPublisher(current_head=report.head_sha, comments=list(existing))
    event_path = workflow_run_fixture(tmp_path, context)
    publish_pr_report(
        event_path,
        context,
        report,
        publisher=publisher,
        display_names={},
        expected_repository=DEFAULT_REPOSITORY,
    )
    assert len(publisher.comments) == 2
    assert publisher.comments[0].body == "<!-- qualock-pr-report -->\nold-1"
    assert publisher.comments[1].id == 2
    assert "old-2" not in publisher.comments[1].body


def test_human_authored_copied_marker_is_ignored(tmp_path: Path) -> None:
    context = context_fixture()
    report = report_fixture(context)
    existing = [
        IssueComment(id=1, author_login="a-human", body="<!-- qualock-pr-report -->\ncopied"),
    ]
    publisher = RecordingPublisher(current_head=report.head_sha, comments=list(existing))
    event_path = workflow_run_fixture(tmp_path, context)
    publish_pr_report(
        event_path,
        context,
        report,
        publisher=publisher,
        display_names={},
        expected_repository=DEFAULT_REPOSITORY,
    )
    assert len(publisher.comments) == 2
    assert publisher.comments[0].author_login == "a-human"
    assert publisher.comments[1].author_login == "github-actions[bot]"


def test_not_applicable_never_comments(tmp_path: Path) -> None:
    context = context_fixture(classification=PrClassification.NOT_APPLICABLE)
    publisher = RecordingPublisher(current_head=context.head_sha)
    event_path = workflow_run_fixture(tmp_path, context)
    publish_pr_report(
        event_path,
        context,
        None,
        publisher=publisher,
        display_names={},
        expected_repository=DEFAULT_REPOSITORY,
    )
    assert publisher.comments == []
    assert publisher.statuses[-1].state == "success"


def test_render_pr_comment_includes_marker_and_verdict() -> None:
    context = context_fixture()
    report = report_fixture(context, verdict=PrReportVerdict.WARN)
    body = render_pr_comment(context, report, {})
    assert body.startswith("<!-- qualock-pr-report -->")
    assert "WARN" in body


def test_publish_propagates_status_write_failure(tmp_path: Path) -> None:
    context = context_fixture()
    report = report_fixture(context)
    publisher = FailingPublisher(current_head=report.head_sha)
    event_path = workflow_run_fixture(tmp_path, context)
    with pytest.raises(GitHubPublishError):
        publish_pr_report(
            event_path,
            context,
            report,
            publisher=publisher,
            display_names={},
            expected_repository=DEFAULT_REPOSITORY,
        )
    assert publisher.comments == []
