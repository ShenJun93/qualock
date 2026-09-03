import base64
import json
from pathlib import Path

import httpx
import pytest

from qualock.github_pr.models import PrClassification
from qualock.github_pr.source import (
    GitHubChangedFile,
    GitHubSourceError,
    HttpxGitHubPrSource,
    PrContextError,
    classify_files,
    parse_pull_request_target_event,
    prepare_pr_context,
)

BASELINE = ".qualock/baseline.lock"
DEFAULT_REPOSITORY = "owner/repo"
DEFAULT_REPOSITORY_ID = 123
DEFAULT_PR_NUMBER = 7
DEFAULT_AUTHOR = "author"


def mock_github_client(
    pages: list[list[str]] | None = None,
    changed_files: int = 0,
    *,
    base_sha: str | None = None,
    head_sha: str | None = None,
    repository: str = DEFAULT_REPOSITORY,
    repository_id: int = DEFAULT_REPOSITORY_ID,
    pr_number: int = DEFAULT_PR_NUMBER,
    author: str = DEFAULT_AUTHOR,
    statuses: dict[str, str] | None = None,
    previous_filenames: dict[str, str] | None = None,
    pr_status_code: int = 200,
    requests_log: list[httpx.Request] | None = None,
) -> httpx.Client:
    pages = pages or []
    resolved_base_sha = base_sha or ("a" * 40)
    resolved_head_sha = head_sha or ("b" * 40)
    statuses = statuses or {}
    previous_filenames = previous_filenames or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if requests_log is not None:
            requests_log.append(request)
        path = request.url.path
        if path == f"/repos/{repository}/pulls/{pr_number}":
            if pr_status_code != 200:
                return httpx.Response(pr_status_code, json={"message": "error"})
            return httpx.Response(
                200,
                json={
                    "number": pr_number,
                    "changed_files": changed_files,
                    "user": {"login": author},
                    "base": {
                        "sha": resolved_base_sha,
                        "repo": {"id": repository_id, "full_name": repository},
                    },
                    "head": {"sha": resolved_head_sha},
                },
            )
        if path == f"/repos/{repository}/pulls/{pr_number}/files":
            page = int(request.url.params.get("page", "1"))
            if page < 1 or page > len(pages):
                return httpx.Response(200, json=[])
            items = [
                {
                    "filename": name,
                    "status": statuses.get(name, "added"),
                    **(
                        {"previous_filename": previous_filenames[name]}
                        if name in previous_filenames
                        else {}
                    ),
                }
                for name in pages[page - 1]
            ]
            return httpx.Response(200, json=items)
        return httpx.Response(404, json={"message": "not found"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def write_pr_target_event(
    tmp_path: Path,
    *,
    base_sha: str,
    head_sha: str,
    repository: str = DEFAULT_REPOSITORY,
    repository_id: int = DEFAULT_REPOSITORY_ID,
    pr_number: int = DEFAULT_PR_NUMBER,
    author: str = DEFAULT_AUTHOR,
    changed_files: int = 1,
    action: str = "opened",
) -> Path:
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "action": action,
                "number": pr_number,
                "pull_request": {
                    "number": pr_number,
                    "changed_files": changed_files,
                    "user": {"login": author},
                    "base": {"sha": base_sha},
                    "head": {"sha": head_sha},
                },
                "repository": {"id": repository_id, "full_name": repository},
            }
        ),
        encoding="utf-8",
    )
    return event_path


class _UnreachableSource:
    def get_pull_request(self, repository: str, pr_number: int) -> object:
        raise AssertionError("must not call GitHub API before event validation")

    def list_changed_files(
        self, repository: str, pr_number: int, *, expected_count: int
    ) -> object:
        raise AssertionError("must not call GitHub API before event validation")

    def read_file_at_ref(
        self, repository: str, path: str, ref: str, *, max_bytes: int
    ) -> object:
        raise AssertionError("must not call GitHub API before event validation")


def test_changed_files_are_paginated_and_sorted() -> None:
    client = mock_github_client(
        pages=[
            ["z.txt"] + [f"path-{i:03}.txt" for i in range(99)],
            [".qualock/baseline.lock"],
        ],
        changed_files=101,
    )
    source = HttpxGitHubPrSource(token="test-token", client=client)
    expected = tuple(
        sorted(["z.txt", ".qualock/baseline.lock"] + [f"path-{i:03}.txt" for i in range(99)])
    )
    files = source.list_changed_files("owner/repo", 7, expected_count=101)
    assert tuple(sorted(item.filename for item in files)) == expected


def test_baseline_only_pr_is_upgrade(tmp_path: Path) -> None:
    base_sha = "a" * 40
    head_sha = "b" * 40
    client = mock_github_client(
        pages=[[".qualock/baseline.lock"]],
        changed_files=1,
        base_sha=base_sha,
        head_sha=head_sha,
    )
    source = HttpxGitHubPrSource(token="test-token", client=client)
    event_path = write_pr_target_event(tmp_path, base_sha=base_sha, head_sha=head_sha)
    context = prepare_pr_context(
        event_path,
        source=source,
        producer_run_id=44,
        expected_repository="owner/repo",
    )
    assert context.classification is PrClassification.UPGRADE
    assert context.changed_paths == (".qualock/baseline.lock",)


def test_ordinary_pr_changed_files_is_not_applicable(tmp_path: Path) -> None:
    base_sha = "c" * 40
    head_sha = "d" * 40
    client = mock_github_client(
        pages=[["README.md"]],
        changed_files=1,
        base_sha=base_sha,
        head_sha=head_sha,
    )
    source = HttpxGitHubPrSource(token="test-token", client=client)
    event_path = write_pr_target_event(tmp_path, base_sha=base_sha, head_sha=head_sha)
    context = prepare_pr_context(
        event_path,
        source=source,
        producer_run_id=1,
        expected_repository="owner/repo",
    )
    assert context.classification is PrClassification.NOT_APPLICABLE
    assert context.changed_paths == ("README.md",)


def test_baseline_plus_other_file_is_invalid_scope() -> None:
    files = (
        GitHubChangedFile(filename="README.md", status="modified"),
        GitHubChangedFile(filename=BASELINE, status="modified"),
    )
    assert classify_files(files) is PrClassification.INVALID_SCOPE


def test_deleted_baseline_only_is_upgrade() -> None:
    files = (GitHubChangedFile(filename=BASELINE, status="removed"),)
    assert classify_files(files) is PrClassification.UPGRADE


def test_renamed_baseline_only_is_upgrade() -> None:
    files = (
        GitHubChangedFile(
            filename="new/path.lock", status="renamed", previous_filename=BASELINE
        ),
    )
    assert classify_files(files) is PrClassification.UPGRADE


def test_pagination_mismatch_raises_source_error() -> None:
    client = mock_github_client(pages=[["a.txt", "b.txt"]], changed_files=5)
    source = HttpxGitHubPrSource(token="test-token", client=client)
    with pytest.raises(GitHubSourceError):
        source.list_changed_files("owner/repo", 7, expected_count=5)


def test_non_2xx_response_raises_source_error() -> None:
    client = mock_github_client(pr_status_code=500)
    source = HttpxGitHubPrSource(token="test-token", client=client)
    with pytest.raises(GitHubSourceError):
        source.get_pull_request("owner/repo", 7)


def test_timeout_raises_source_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = HttpxGitHubPrSource(token="test-token", client=client)
    with pytest.raises(GitHubSourceError):
        source.get_pull_request("owner/repo", 7)


def test_redirect_response_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://evil.example/pwn"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = HttpxGitHubPrSource(token="test-token", client=client)
    with pytest.raises(GitHubSourceError):
        source.get_pull_request("owner/repo", 7)


def test_malformed_event_json_raises_context_error(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(PrContextError):
        parse_pull_request_target_event(event_path, expected_repository="owner/repo")


def test_event_repository_mismatch_is_rejected_before_api_access(tmp_path: Path) -> None:
    event_path = write_pr_target_event(
        tmp_path,
        base_sha="a" * 40,
        head_sha="b" * 40,
        repository="owner/other-repo",
    )
    with pytest.raises(PrContextError):
        prepare_pr_context(
            event_path,
            source=_UnreachableSource(),  # type: ignore[arg-type]
            producer_run_id=1,
            expected_repository="owner/repo",
        )


def test_event_and_api_identity_mismatch_raises_context_error(tmp_path: Path) -> None:
    client = mock_github_client(
        pages=[["README.md"]],
        changed_files=1,
        base_sha="e" * 40,
        head_sha="f" * 40,
    )
    source = HttpxGitHubPrSource(token="test-token", client=client)
    event_path = write_pr_target_event(tmp_path, base_sha="1" * 40, head_sha="2" * 40)
    with pytest.raises(PrContextError):
        prepare_pr_context(
            event_path,
            source=source,
            producer_run_id=1,
            expected_repository="owner/repo",
        )


def test_contents_response_over_raw_limit_raises_source_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (262_144 + 1))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = HttpxGitHubPrSource(token="test-token", client=client)
    with pytest.raises(GitHubSourceError):
        source.read_file_at_ref("owner/repo", BASELINE, "b" * 40, max_bytes=131_072)


def test_decoded_proposed_lock_over_limit_raises_source_error() -> None:
    decoded = b"0" * 140_000
    encoded = base64.b64encode(decoded).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"type": "file", "encoding": "base64", "content": encoded, "size": len(decoded)},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = HttpxGitHubPrSource(token="test-token", client=client)
    with pytest.raises(GitHubSourceError):
        source.read_file_at_ref("owner/repo", BASELINE, "b" * 40, max_bytes=131_072)


def test_contents_response_non_file_type_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "dir", "entries": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = HttpxGitHubPrSource(token="test-token", client=client)
    with pytest.raises(GitHubSourceError):
        source.read_file_at_ref("owner/repo", BASELINE, "b" * 40, max_bytes=131_072)


def test_contents_response_malformed_base64_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"type": "file", "encoding": "base64", "content": "@@@not-base64@@@"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = HttpxGitHubPrSource(token="test-token", client=client)
    with pytest.raises(GitHubSourceError):
        source.read_file_at_ref("owner/repo", BASELINE, "b" * 40, max_bytes=131_072)


def test_source_never_opens_a_real_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("real network access is forbidden in tests")

    monkeypatch.setattr("socket.socket.connect", fail_connect)
    client = mock_github_client(pages=[["README.md"]], changed_files=1)
    source = HttpxGitHubPrSource(token="test-token", client=client)
    identity = source.get_pull_request("owner/repo", 7)
    assert identity.repository_full_name == "owner/repo"


def test_get_pull_request_uses_required_headers_and_api_version() -> None:
    captured: list[httpx.Request] = []
    client = mock_github_client(pages=[["README.md"]], changed_files=1, requests_log=captured)
    source = HttpxGitHubPrSource(token="test-token", client=client)
    source.get_pull_request("owner/repo", 7)
    assert len(captured) == 1
    request = captured[0]
    assert request.headers["accept"] == "application/vnd.github+json"
    assert request.headers["x-github-api-version"] == "2022-11-28"
    assert request.headers["authorization"] == "Bearer test-token"
