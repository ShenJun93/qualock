import base64
import binascii
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import httpx

from qualock.github_pr.models import PrClassification, PullRequestContext

_API_BASE_URL = "https://api.github.com"
_API_VERSION = "2022-11-28"
_METADATA_MAX_BYTES = 1_048_576
_CONTENTS_MAX_BYTES = 262_144
_MAX_FILENAME_LENGTH = 4096
_BASELINE_PATH = ".qualock/baseline.lock"
_PER_PAGE = 100


class GitHubSourceError(Exception):
    """Raised when the GitHub REST API cannot be trusted as read."""


class PrContextError(Exception):
    """Raised when a trusted pull request context cannot be established."""


@dataclass(frozen=True)
class GitHubPrIdentity:
    repository_id: int
    repository_full_name: str
    pr_number: int
    pr_author_login: str
    base_sha: str
    head_sha: str
    changed_files: int


@dataclass(frozen=True)
class GitHubChangedFile:
    filename: str
    status: Literal["added", "modified", "removed", "renamed", "copied", "changed", "unchanged"]
    previous_filename: str | None = None


class GitHubPrSource(Protocol):
    def get_pull_request(self, repository: str, pr_number: int) -> GitHubPrIdentity: ...

    def list_changed_files(
        self, repository: str, pr_number: int, *, expected_count: int
    ) -> tuple[GitHubChangedFile, ...]: ...

    def read_file_at_ref(
        self, repository: str, path: str, ref: str, *, max_bytes: int
    ) -> bytes: ...


class HttpxGitHubPrSource:
    def __init__(self, token: str, *, client: httpx.Client | None = None) -> None:
        self._token = token
        self._client = client or httpx.Client(follow_redirects=False, timeout=15.0)

    def get_pull_request(self, repository: str, pr_number: int) -> GitHubPrIdentity:
        body = self._request_bytes(
            "GET",
            f"/repos/{repository}/pulls/{pr_number}",
            params=None,
            max_bytes=_METADATA_MAX_BYTES,
        )
        try:
            payload = json.loads(body)
            return GitHubPrIdentity(
                repository_id=int(payload["base"]["repo"]["id"]),
                repository_full_name=str(payload["base"]["repo"]["full_name"]),
                pr_number=int(payload["number"]),
                pr_author_login=str(payload["user"]["login"]),
                base_sha=str(payload["base"]["sha"]),
                head_sha=str(payload["head"]["sha"]),
                changed_files=int(payload["changed_files"]),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise GitHubSourceError(
                f"malformed pull request response for {repository}#{pr_number}"
            ) from error

    def list_changed_files(
        self, repository: str, pr_number: int, *, expected_count: int
    ) -> tuple[GitHubChangedFile, ...]:
        collected: list[GitHubChangedFile] = []
        page = 1
        while True:
            body = self._request_bytes(
                "GET",
                f"/repos/{repository}/pulls/{pr_number}/files",
                params={"per_page": _PER_PAGE, "page": page},
                max_bytes=_METADATA_MAX_BYTES,
            )
            try:
                items = json.loads(body)
                if not isinstance(items, list):
                    raise TypeError("changed-files response is not a list")
                for item in items:
                    previous_filename = item.get("previous_filename")
                    collected.append(
                        GitHubChangedFile(
                            filename=str(item["filename"])[:_MAX_FILENAME_LENGTH],
                            status=item["status"],
                            previous_filename=(
                                str(previous_filename)[:_MAX_FILENAME_LENGTH]
                                if previous_filename is not None
                                else None
                            ),
                        )
                    )
            except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as error:
                raise GitHubSourceError(
                    f"malformed changed-files response for {repository}#{pr_number}"
                ) from error
            if len(items) < _PER_PAGE:
                break
            page += 1
        if len(collected) != expected_count:
            raise GitHubSourceError("changed-file pagination incomplete")
        return tuple(collected)

    def read_file_at_ref(
        self, repository: str, path: str, ref: str, *, max_bytes: int
    ) -> bytes:
        body = self._request_bytes(
            "GET",
            f"/repos/{repository}/contents/{path}",
            params={"ref": ref},
            max_bytes=_CONTENTS_MAX_BYTES,
        )
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise GitHubSourceError(
                f"malformed contents response for {repository}:{path}@{ref}"
            ) from error
        if not isinstance(payload, dict) or payload.get("type") != "file":
            raise GitHubSourceError(f"{path} at {ref} is not a regular file")
        if payload.get("encoding") != "base64":
            raise GitHubSourceError(f"{path} at {ref} is not base64-encoded")
        content = payload.get("content")
        if not isinstance(content, str):
            raise GitHubSourceError(f"{path} at {ref} has no content")
        try:
            decoded = base64.b64decode(content, validate=False)
        except binascii.Error as error:
            raise GitHubSourceError(
                f"{path} at {ref} has malformed base64 content"
            ) from error
        if len(decoded) > max_bytes:
            raise GitHubSourceError(f"{path} at {ref} exceeds {max_bytes} byte limit")
        return decoded

    def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None,
        max_bytes: int,
    ) -> bytes:
        url = f"{_API_BASE_URL}{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }
        try:
            with self._client.stream(
                method, url, params=params, headers=headers
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise GitHubSourceError(
                        f"GitHub API returned status {response.status_code} for {path}"
                    )
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared = int(content_length)
                    except ValueError as error:
                        raise GitHubSourceError(
                            f"invalid content-length header for {path}"
                        ) from error
                    if declared > max_bytes:
                        raise GitHubSourceError(
                            f"response for {path} exceeds {max_bytes} byte limit"
                        )
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise GitHubSourceError(
                            f"response for {path} exceeds {max_bytes} byte limit"
                        )
                return bytes(body)
        except httpx.TimeoutException as error:
            raise GitHubSourceError(f"timed out requesting {path}") from error
        except httpx.TransportError as error:
            raise GitHubSourceError(f"transport error requesting {path}") from error


def classify_files(files: tuple[GitHubChangedFile, ...]) -> PrClassification:
    baseline = _BASELINE_PATH
    touches_baseline = tuple(
        item
        for item in files
        if item.filename == baseline or item.previous_filename == baseline
    )
    if not touches_baseline:
        return PrClassification.NOT_APPLICABLE
    if len(files) == 1:
        return PrClassification.UPGRADE
    return PrClassification.INVALID_SCOPE


def parse_pull_request_target_event(
    event_path: Path, *, expected_repository: str
) -> GitHubPrIdentity:
    try:
        text = event_path.read_text(encoding="utf-8")
    except OSError as error:
        raise PrContextError(f"cannot read event file {event_path}: {error}") from error
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise PrContextError(f"malformed event JSON in {event_path}") from error
    if not isinstance(payload, dict):
        raise PrContextError(f"malformed event JSON in {event_path}")
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        raise PrContextError(f"missing repository in event {event_path}")
    repository_full_name = repository.get("full_name")
    if repository_full_name != expected_repository:
        raise PrContextError(
            f"event repository {repository_full_name!r} does not match "
            f"expected {expected_repository!r}"
        )
    action = payload.get("action")
    pull_request = payload.get("pull_request")
    if not isinstance(action, str) or not action or not isinstance(pull_request, dict):
        raise PrContextError(f"unexpected event shape in {event_path}")
    try:
        return GitHubPrIdentity(
            repository_id=int(repository["id"]),
            repository_full_name=str(repository_full_name),
            pr_number=int(pull_request["number"]),
            pr_author_login=str(pull_request["user"]["login"]),
            base_sha=str(pull_request["base"]["sha"]),
            head_sha=str(pull_request["head"]["sha"]),
            changed_files=int(pull_request["changed_files"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PrContextError(f"unexpected event shape in {event_path}") from error


def prepare_pr_context(
    event_path: Path,
    *,
    source: GitHubPrSource,
    producer_run_id: int,
    expected_repository: str,
) -> PullRequestContext:
    event_identity = parse_pull_request_target_event(
        event_path, expected_repository=expected_repository
    )
    try:
        api_identity = source.get_pull_request(expected_repository, event_identity.pr_number)
    except GitHubSourceError as error:
        raise PrContextError(
            f"cannot fetch pull request {expected_repository}#{event_identity.pr_number}"
        ) from error
    if (
        api_identity.repository_id != event_identity.repository_id
        or api_identity.repository_full_name != event_identity.repository_full_name
        or api_identity.pr_number != event_identity.pr_number
        or api_identity.base_sha != event_identity.base_sha
        or api_identity.head_sha != event_identity.head_sha
    ):
        raise PrContextError(
            f"event and API pull request identity mismatch for "
            f"{expected_repository}#{event_identity.pr_number}"
        )
    try:
        files = source.list_changed_files(
            expected_repository,
            api_identity.pr_number,
            expected_count=api_identity.changed_files,
        )
    except GitHubSourceError as error:
        raise PrContextError(
            f"cannot establish complete changed-file list for "
            f"{expected_repository}#{api_identity.pr_number}"
        ) from error
    classification = classify_files(files)
    return PullRequestContext(
        repository_id=api_identity.repository_id,
        repository_full_name=api_identity.repository_full_name,
        pr_number=api_identity.pr_number,
        pr_author_login=api_identity.pr_author_login,
        base_sha=api_identity.base_sha,
        head_sha=api_identity.head_sha,
        producer_run_id=producer_run_id,
        changed_paths=tuple(sorted(item.filename for item in files)),
        classification=classification,
    )
