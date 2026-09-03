# GitHub Pull-Request Qualification Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a secure, low-tech GitHub PR gate that automatically qualifies baseline-only Codex upgrade PRs, publishes a trusted `qualock/pr` status plus one sticky comment, and never executes PR-controlled code in a privileged workflow.

**Architecture:** A trusted `pull_request_target` producer reads PR metadata and the proposed baseline lock strictly as bounded GitHub API data, delegates any real agent comparison to the existing `execute_check`, and emits only sanitized JSON artifacts. A separate `workflow_run` reporter has GitHub write permissions but no model credential; it validates producer artifacts against the triggering run and live PR before writing `qualock/pr` or updating the bot-owned sticky comment. `qualock github setup` only generates the two pinned workflow files locally.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, httpx, PyYAML, GitHub Actions, GitHub REST API, pytest, Ruff, strict mypy.

**Spec:** `docs/superpowers/specs/2026-09-03-github-pr-qualification-reports-design.md`

## Global Constraints

- Branch: `feat/github-pr-qualification-reports`; approved spec commit: `e88ff5e3c1c02104765307c888c87a8f2032ee8d`; base main: `f5a83da8a625fdfa27b7cd75ec07c7bced058e1c`.
- V1 qualifies only PRs whose sole changed path is `.qualock/baseline.lock`; ordinary PRs are `not_applicable`, and baseline-plus-other-file PRs are fail-closed `invalid_scope`.
- Privileged workflows must never checkout, fetch, import, source, execute, install dependencies from, or interpolate shell fragments from PR-controlled code or metadata.
- Producer uses `pull_request_target`, trusted base checkout, model credential, read-only GitHub permissions, and no status/comment write permission.
- Reporter uses `workflow_run`, no model credential, validates artifacts as untrusted data, and alone owns `statuses: write` plus `pull-requests: write`.
- Real qualification must call existing `qualock.commands.execute_check`; do not modify `qualification/policy.py`, `qualification/models.py`, Docker policy, source materialization, or evidence policy to create a GitHub-specific engine.
- Preserve QuaLock verdicts exactly. GitHub mapping is `not_applicable/PASS -> success`, `WARN/BLOCK -> failure`, `INCOMPLETE/report failure -> error`.
- No raw `AttemptResult.events_jsonl`, prompt/task content, stdout/stderr, auth material, token, arbitrary PR text, or source-code content may enter `pr-report.json` or the sticky comment.
- Proposed candidate version must be exact stable `X.Y.Z`, strictly newer, with trusted suite/config/model/canary identity and exact resolved binary SHA256.
- Proposed `qualock_version` must equal the trusted runtime version. `created_at` is parseable provenance only. Proposed historical counters never determine the live PR verdict.
- Setup is local-only and idempotent: no git add/commit/push, no GitHub setting mutation, no secret creation.
- Generated workflow `uses:` pins are immutable full SHAs verified on 2026-09-03:
  - `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1` (`v7.0.1`)
  - `actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97` (`v7.0.0`)
  - `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` (`v7.0.1`)
  - `actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` (`v8.0.1`)
- No release, tag, GitHub Release, PyPI action, remote push, PR creation, or merge is part of this implementation plan.

## File Structure

Create one focused package, `src/qualock/github_pr/`:

- `models.py`: strict/frozen PR context, sanitized report, reason-code, and setup models.
- `report.py`: bounded serialization, atomic read/write, conversion from `QualificationResult`, fixed safe rendering inputs.
- `source.py`: read-only GitHub PR REST boundary, event parsing, changed-file pagination, exact-ref baseline retrieval, classification.
- `commands.py`: proposed-lock validation plus trusted producer orchestration; the only place that delegates to `execute_check`.
- `publisher.py`: reporter event/artifact validation, live-head race check, commit status, bot-owned sticky comment.
- `templates.py`: immutable producer/reporter workflow strings and static action pins.
- `setup.py`: local-only idempotent workflow installation.
- `__init__.py`: package docstring only.

Modify `src/qualock/cli.py` only for a `github` subgroup and thin command wiring. Tests mirror each unit under `tests/unit/test_github_pr_*.py`. README/ROADMAP move only after code and behavior are complete.

---

### Task 1: Strict Context and Sanitized Report Contracts

**Files:**
- Create: `src/qualock/github_pr/__init__.py`
- Create: `src/qualock/github_pr/models.py`
- Create: `src/qualock/github_pr/report.py`
- Test: `tests/unit/test_github_pr_models.py`
- Test: `tests/unit/test_github_pr_report.py`

**Interfaces:**
- Produces: `PrClassification`, `PrReportVerdict`, `PrReasonCode`, `PrCanarySummary`, `PullRequestContext`, `PullRequestReport`, `PrArtifactError`, `write_context`, `read_context`, `write_report`, `read_report`, `report_from_qualification`, `not_applicable_report`, `incomplete_report`.
- Consumes: existing `QualificationResult`, `Verdict`, and `qualock.__version__`; no network or GitHub API dependency.

- [ ] **Step 1: Write failing model tests for strict frozen schema and bounded fields**

Create tests that instantiate a valid context/report, reject extra keys, reject non-40-hex SHAs, reject invalid repository names and negative IDs/counts, and prove Pydantic model instances are frozen.

```python
from pydantic import ValidationError

from qualock.github_pr.models import (
    PrClassification,
    PrReportVerdict,
    PullRequestContext,
)


def valid_context() -> PullRequestContext:
    return PullRequestContext(
        repository_id=123,
        repository_full_name="owner/repo",
        pr_number=17,
        pr_author_login="alice",
        base_sha="a" * 40,
        head_sha="b" * 40,
        producer_run_id=999,
        changed_paths=(".qualock/baseline.lock",),
        classification=PrClassification.UPGRADE,
    )


def test_context_is_strict_and_frozen() -> None:
    context = valid_context()
    assert context.schema_version == 1
    assert context.classification is PrClassification.UPGRADE
    with pytest.raises(ValidationError):
        PullRequestContext.model_validate({**context.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError):
        PullRequestContext.model_validate({**context.model_dump(), "head_sha": "short"})
```

- [ ] **Step 2: Run the model tests and verify RED**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_github_pr_models.py
```

Expected: import failure because `qualock.github_pr.models` does not exist.

- [ ] **Step 3: Implement the strict models with enums instead of arbitrary public reason strings**

Use Pydantic because context/report JSON crosses a trust boundary. Every model uses `ConfigDict(frozen=True, extra="forbid")`.

```python
class PrClassification(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    UPGRADE = "upgrade"
    INVALID_SCOPE = "invalid_scope"


class PrReportVerdict(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"
    INCOMPLETE = "incomplete"


class PrReasonCode(str, Enum):
    INVALID_SCOPE = "invalid_scope"
    INVALID_PROPOSED_LOCK = "invalid_proposed_lock"
    PROPOSED_LOCK_UNAVAILABLE = "proposed_lock_unavailable"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    TRUSTED_BASELINE_STALE = "trusted_baseline_stale"
    QUALIFICATION_FAILED = "qualification_failed"
    INSUFFICIENT_VALID_ATTEMPTS = "insufficient_valid_attempts"
    UNSTABLE_BASELINE = "unstable_baseline"
    QUALITY_REGRESSION = "quality_regression"
    CRITICAL_REGRESSION = "critical_regression"


_SHA_PATTERN = r"^[0-9a-f]{40}$"
_REPOSITORY_PATTERN = r"^[^/\s]+/[^/\s]+$"


class PrCanarySummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    canary_id: str = Field(min_length=1, max_length=256)
    baseline_successes: int = Field(ge=0)
    baseline_valid: int = Field(ge=0)
    candidate_successes: int = Field(ge=0)
    candidate_valid: int = Field(ge=0)
    verdict: Verdict


class PullRequestContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    repository_id: int = Field(gt=0)
    repository_full_name: str = Field(pattern=_REPOSITORY_PATTERN, max_length=256)
    pr_number: int = Field(gt=0)
    pr_author_login: str = Field(min_length=1, max_length=100)
    base_sha: str = Field(pattern=_SHA_PATTERN)
    head_sha: str = Field(pattern=_SHA_PATTERN)
    producer_run_id: int = Field(gt=0)
    changed_paths: tuple[str, ...]
    classification: PrClassification


class PullRequestReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    repository_id: int = Field(gt=0)
    repository_full_name: str = Field(pattern=_REPOSITORY_PATTERN, max_length=256)
    pr_number: int = Field(gt=0)
    base_sha: str = Field(pattern=_SHA_PATTERN)
    head_sha: str = Field(pattern=_SHA_PATTERN)
    producer_run_id: int = Field(gt=0)
    classification: PrClassification
    baseline_version: str | None = None
    candidate_version: str | None = None
    qualification_id: str | None = None
    qualock_version: str
    verdict: PrReportVerdict
    canaries: tuple[PrCanarySummary, ...] = ()
    reason_codes: tuple[PrReasonCode, ...] = ()
    credential_unavailable: bool = False
    qualification_completed: bool = False
```

These are the complete cross-workflow schemas. No extra free-form public reason field is allowed; `PrCanarySummary` carries only aggregate counts and existing `Verdict`.

- [ ] **Step 4: Write failing report tests for sanitization, atomic writes, and byte caps**

Build a `QualificationResult` whose `AttemptResult.events_jsonl` includes sentinel secrets such as `SECRET_TRANSCRIPT`, convert it, serialize it, and assert none of these strings occur:

```python
for forbidden in (
    "events_jsonl",
    "SECRET_TRANSCRIPT",
    "authorization",
    "GITHUB_TOKEN",
    "task body",
):
    assert forbidden not in encoded
```

Also test:

- `write_context` and `write_report` use same-directory temp files then `Path.replace`;
- no `.*.tmp` file remains after success;
- `read_context(..., max_bytes=131_072)` and `read_report(..., max_bytes=262_144)` reject oversized input before JSON parsing;
- missing/invalid JSON raises typed `PrArtifactError` rather than returning partial data.

- [ ] **Step 5: Implement bounded report conversion and atomic storage**

Use one private atomic helper:

```python
def _atomic_write(path: Path, payload: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(payload.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
```

`report_from_qualification(context, result)` must map only `result.verdict`, per-canary aggregate fields, `qualification_id`, baseline/candidate version, and deterministic reason codes. It must never call `dataclasses.asdict(result)` or copy `execution.reason` / `result.reasons`. Derive bounded codes mechanically from each execution, preserving first-seen order and deduplicating:

```python
def _reason_code(execution: CanaryExecution) -> PrReasonCode | None:
    if execution.verdict is Verdict.INCOMPLETE:
        return PrReasonCode.INSUFFICIENT_VALID_ATTEMPTS
    if execution.verdict is Verdict.BLOCK:
        return PrReasonCode.CRITICAL_REGRESSION
    if execution.verdict is Verdict.WARN:
        if execution.baseline_successes < execution.baseline_valid:
            return PrReasonCode.UNSTABLE_BASELINE
        return PrReasonCode.QUALITY_REGRESSION
    return None
```

PASS executions contribute no reason code. The top-level report verdict is `PrReportVerdict(result.verdict.value)`, so QuaLock semantics are preserved exactly.

- [ ] **Step 6: Run Task 1 tests, Ruff, and strict mypy**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_github_pr_models.py tests/unit/test_github_pr_report.py
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/github_pr/models.py src/qualock/github_pr/report.py \
  tests/unit/test_github_pr_models.py tests/unit/test_github_pr_report.py
/tmp/qualock-static-22-final/bin/mypy --strict \
  src/qualock/github_pr/models.py src/qualock/github_pr/report.py
```

Expected: all PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add src/qualock/github_pr tests/unit/test_github_pr_models.py tests/unit/test_github_pr_report.py
git commit -m "feat: add sanitized GitHub PR report contracts"
```

---

### Task 2: Read-Only GitHub PR Source and Trusted Context Preparation

**Files:**
- Create: `src/qualock/github_pr/source.py`
- Create: `tests/unit/test_github_pr_source.py`

**Interfaces:**
- Consumes: `PullRequestContext`, `PrClassification`, Task 1 artifact writers.
- Produces: `GitHubSourceError`, `PrContextError`, `GitHubPrIdentity`, `GitHubChangedFile`, `GitHubPrSource` Protocol, `HttpxGitHubPrSource`, `parse_pull_request_target_event`, `prepare_pr_context`; both accept a trusted `expected_repository` string and reject event mismatch before GitHub API access.
- Network contract: GitHub REST API version header `X-GitHub-Api-Version: 2022-11-28`, `Accept: application/vnd.github+json`, bounded 15-second timeout, explicit pagination, no arbitrary redirect following.

- [ ] **Step 1: Write failing source tests with a fake transport**

Cover exact calls and security behavior. Define two local helpers in this test file: `mock_github_client(pages, changed_files)` uses `httpx.MockTransport`, returns deterministic JSON for `GET /repos/owner/repo/pulls/7` plus the requested `GET /repos/owner/repo/pulls/7/files?page=N`, records every request, and never opens a socket; `write_pr_target_event(tmp_path, *, base_sha, head_sha)` writes the minimal trusted event JSON for `owner/repo` PR #7 and returns its `Path`. Then use the injected client:

```python
def test_changed_files_are_paginated_and_sorted() -> None:
    client = mock_github_client(
        pages=[
            ["z.txt"] + [f"path-{i:03}.txt" for i in range(99)],
            [".qualock/baseline.lock"],
        ],
        changed_files=101,
    )
    source = HttpxGitHubPrSource(token="test-token", client=client)
    expected = tuple(sorted(["z.txt", ".qualock/baseline.lock"] + [f"path-{i:03}.txt" for i in range(99)]))
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
```

Also test ordinary PR, baseline-plus-other-file invalid scope, deleted/renamed baseline, pagination mismatch, HTTP non-2xx, timeout, redirect response rejection, malformed event JSON, repository mismatch, raw contents-API response above 262144 bytes, and decoded proposed lock size >131072 bytes.

- [ ] **Step 2: Run source tests and verify RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_github_pr_source.py
```

Expected: import failure for `qualock.github_pr.source`.

- [ ] **Step 3: Implement event parsing and a narrow read-only Protocol**

```python
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
```

`parse_pull_request_target_event(event_path, *, expected_repository)` reads only the local event JSON file, first requires event repository full name to equal the trusted expected repository, then requires action/event shape, repository numeric ID/full name, PR number, author login, base SHA, and head SHA. It never returns title/body/branch names because later code does not need them.

- [ ] **Step 4: Implement `HttpxGitHubPrSource` with complete pagination checks**

`HttpxGitHubPrSource(token: str, *, client: httpx.Client | None = None)` accepts an injected client only for tests; production creates `httpx.Client(follow_redirects=False, timeout=15.0)`. Implement a private streaming `_request_bytes(method, url, *, params, max_bytes)` that rejects non-2xx, rejects redirects, checks `Content-Length` when present, and stops streaming once the bounded byte limit is exceeded before any JSON parse. Use a 1048576-byte cap for PR metadata/file-list pages and 262144 bytes for the contents response. For `/pulls/{n}/files`, request `per_page=100&page=N`, parse only bounded `filename`, `status`, and optional `previous_filename` fields into `GitHubChangedFile`, and compare final count to the explicit `expected_count` passed from the already-validated pull request identity. If counts differ, raise `GitHubSourceError("changed-file pagination incomplete")`; do not guess classification.

For `.qualock/baseline.lock`, call only the fixed contents endpoint for `.qualock/baseline.lock` with exact `ref=head_sha`; parse the already-capped response JSON, then reject non-file, non-base64, malformed base64, and decoded size above 131072 bytes.

- [ ] **Step 5: Implement classification without shell interpretation**

```python
def classify_files(files: tuple[GitHubChangedFile, ...]) -> PrClassification:
    baseline = ".qualock/baseline.lock"
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
```

`prepare_pr_context(..., expected_repository=...)` re-fetches PR identity from API, requires event/API repository+PR+base+head consistency, obtains the complete changed-file records, classifies with `classify_files`, and stores `changed_paths=tuple(sorted(item.filename for item in files))` in the cross-workflow context. A rename away from the baseline still classifies as UPGRADE because `previous_filename` touches the baseline; the later exact-head read then fails closed instead of silently becoming NOT_APPLICABLE. If complete classification cannot be established, raise `PrContextError`; Workflow A will fail without guessing a status target.

- [ ] **Step 6: Run Task 2 verification**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_github_pr_models.py tests/unit/test_github_pr_report.py tests/unit/test_github_pr_source.py
/tmp/qualock-static-22-final/bin/ruff check src/qualock/github_pr/source.py tests/unit/test_github_pr_source.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/github_pr/source.py
```

Expected: PASS with no real github.com access.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/qualock/github_pr/source.py tests/unit/test_github_pr_source.py
git commit -m "feat: classify GitHub agent upgrade PRs safely"
```
---

### Task 3: Proposed-Lock Validation and Producer Qualification Orchestration

**Files:**
- Create: `src/qualock/github_pr/commands.py`
- Create: `tests/unit/test_github_pr_commands.py`

**Interfaces:**
- Consumes: `GitHubPrSource`, Task 1 context/report models, current `BaselineLock`, `load_project`, `suite_fingerprint`, `config_fingerprint`, `CodexResolver`, `execute_check`.
- Produces: `PrValidationError`, `CandidateRequest`, `validate_proposed_lock`, `prepare_pr`, `qualify_prepared_pr`, `PreparePrOutcome`.
- Critical invariant: only `qualify_prepared_pr` may call `execute_check`, and it always passes the trusted checkout root plus `codex@<exact-version>`.

- [ ] **Step 1: Write RED tests for proposed-lock validation**

Define a local `ProjectFixture` dataclass in this test file with `root: Path`, a recording resolver fake exposing `resolve_calls`, and `proposed_lock_json(**overrides) -> bytes`; its constructor writes a trusted config, canary, grader patch, and current baseline lock under `tmp_path`. Build proposed `BaselineLock` bytes from that trusted fixture. Parameterize every rejected mutation:

```python
@pytest.mark.parametrize(
    "candidate_version",
    ["latest", "0.152.0-beta.1", "0.152.0+build.1", "0.151.0", "0.150.0"],
)
def test_non_stable_or_not_newer_candidate_is_rejected(
    project_fixture: ProjectFixture, candidate_version: str
) -> None:
    raw = project_fixture.proposed_lock_json(candidate_version=candidate_version)
    with pytest.raises(PrValidationError):
        validate_proposed_lock(project_fixture.root, raw, resolver=project_fixture.resolver)
    assert project_fixture.resolver.resolve_calls == []
```

Add dedicated tests for wrong model pin, missing/extra canary ID, `successes > valid_runs`, counts above configured repetitions, unstable critical canary, unparseable `created_at`, and candidate binary SHA mismatch.

- [ ] **Step 2: Verify validation tests are RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_github_pr_commands.py -k proposed_lock
```

Expected: missing `validate_proposed_lock` / `CandidateRequest`.

- [ ] **Step 3: Implement candidate validation using only trusted project state**

```python
@dataclass(frozen=True)
class CandidateRequest:
    version: str
    binary_sha256: str


def validate_proposed_lock(
    root: Path,
    raw: bytes,
    *,
    resolver: Resolver,
) -> CandidateRequest:
    proposed = BaselineLock.model_validate_json(raw)
    config, canaries = load_project(root)
    trusted = read_baseline_lock(project_dir(root) / "baseline.lock")
    suite_sha = suite_fingerprint(canaries)
    config_sha = config_fingerprint(config)
    assert_suite_fresh(trusted, suite_sha, config_sha)
    # exact-stable version, strict newer-than-baseline, fingerprints,
    # model pin, canary identity/counters, runtime version, timestamp,
    # and resolved binary SHA checks all happen here.
```

Use a dedicated exact-stable regex `^\d+\.\d+\.\d+$` and numeric `packaging.version.Version` ordering. Require:

- `proposed.agent.name == trusted.agent.name == config.agent.name == "codex"`;
- trusted baseline first passes `assert_suite_fresh(trusted, suite_sha, config_sha)`;
- proposed `suite_sha256 == suite_sha`;
- proposed `config_sha256 == config_sha`;
- proposed model fields equal `config.model.id`, `config.model.snapshot`, and `config.model.reasoning_effort`;
- proposed canary keys equal `{canary.id for canary in canaries}`;
- every counter satisfies `0 <= successes <= valid_runs <= config.qualification.repetitions`;
- each critical canary has both values exactly equal to configured repetitions;
- `proposed.qualock_version == qualock.__version__`;
- `datetime.fromisoformat(proposed.created_at)` succeeds;
- resolver's exact candidate `binary.sha256 == proposed.agent.binary_sha256`.

Raise typed `PrValidationError` without embedding raw proposed JSON or token-bearing exception bodies in the public report.

- [ ] **Step 4: Write RED orchestration tests for all three classifications and failure paths**

Required assertions:

- NOT_APPLICABLE writes context + `not_applicable` report and never calls resolver/check;
- INVALID_SCOPE writes context + INCOMPLETE report with `INVALID_SCOPE` and never calls resolver/check;
- UPGRADE `prepare_pr` returns context plus proposed lock bytes as data; if the exact-head baseline read fails because the file was deleted/renamed/missing/malformed at the fixed endpoint, it returns the already-established context plus INCOMPLETE/`INVALID_PROPOSED_LOCK` and never calls resolver/check;
- UPGRADE + valid lock + credential available calls `execute_check(root, "codex@0.152.0", resolver=resolver)` exactly once;
- PASS/WARN/BLOCK/INCOMPLETE are copied unchanged to `PullRequestReport`;
- missing credential writes INCOMPLETE with `CREDENTIAL_UNAVAILABLE`, no check;
- stale trusted baseline writes INCOMPLETE with `TRUSTED_BASELINE_STALE`, no candidate resolve/check;
- validation failure writes INCOMPLETE with `INVALID_PROPOSED_LOCK`, no check;
- `execute_check` exception writes INCOMPLETE with `QUALIFICATION_FAILED`, with `qualification_completed=False` and no fabricated qualification ID;
- report write failure propagates so the producer job fails while an already-written context remains truthful.

- [ ] **Step 5: Implement two-phase producer orchestration**

`prepare_pr` runs before model auth materialization:

```python
@dataclass(frozen=True)
class PreparePrOutcome:
    context: PullRequestContext
    proposed_lock: bytes | None
    terminal_report: PullRequestReport | None


def prepare_pr(
    root: Path,
    event_path: Path,
    *,
    source: GitHubPrSource,
    producer_run_id: int,
    expected_repository: str,
) -> PreparePrOutcome:
    context = prepare_pr_context(
        event_path,
        source=source,
        producer_run_id=producer_run_id,
        expected_repository=expected_repository,
    )
    if context.classification is PrClassification.NOT_APPLICABLE:
        return PreparePrOutcome(context, None, not_applicable_report(context))
    if context.classification is PrClassification.INVALID_SCOPE:
        return PreparePrOutcome(context, None, incomplete_report(context, PrReasonCode.INVALID_SCOPE))
    try:
        raw = source.read_file_at_ref(
            context.repository_full_name,
            ".qualock/baseline.lock",
            context.head_sha,
            max_bytes=131_072,
        )
    except GitHubSourceError:
        return PreparePrOutcome(
            context,
            None,
            incomplete_report(context, PrReasonCode.INVALID_PROPOSED_LOCK),
        )
    return PreparePrOutcome(context, raw, None)
```

Rules:

- always classify first;
- `not_applicable` returns an immediate sanitized success report;
- `invalid_scope` returns immediate INCOMPLETE/`INVALID_SCOPE`;
- `upgrade` reads the exact-head baseline bytes and returns them but does not resolve Codex or call `execute_check`; a typed fixed-file read failure after context establishment becomes immediate INCOMPLETE/`INVALID_PROPOSED_LOCK`, preserving truthful PR/head identity for the reporter.

`qualify_prepared_pr` has the exact boundary below:

```python
class CheckExecutor(Protocol):
    def __call__(
        self,
        root: Path,
        candidate_spec: str,
        *,
        resolver: Resolver | None = None,
    ) -> QualificationResult: ...


def qualify_prepared_pr(
    root: Path,
    context: PullRequestContext,
    proposed_lock: bytes,
    *,
    credential_available: bool,
    resolver: Resolver | None = None,
    check_executor: CheckExecutor = execute_check,
) -> PullRequestReport:
    if not credential_available:
        return incomplete_report(context, PrReasonCode.CREDENTIAL_UNAVAILABLE)
    resolver = resolver or _default_resolver()
    try:
        candidate = validate_proposed_lock(root, proposed_lock, resolver=resolver)
        result = check_executor(root, f"codex@{candidate.version}", resolver=resolver)
    except BaselineStaleError:
        return incomplete_report(context, PrReasonCode.TRUSTED_BASELINE_STALE)
    except PrValidationError:
        return incomplete_report(context, PrReasonCode.INVALID_PROPOSED_LOCK)
    except Exception:
        return incomplete_report(context, PrReasonCode.QUALIFICATION_FAILED)
    return report_from_qualification(context, result)
```

The broad `except Exception` is intentional at this producer boundary: any qualification/runtime failure becomes bounded INCOMPLETE, but `str(exc)` is never copied into the report. Artifact-storage failures are outside this function and therefore still propagate from the CLI writer.

- [ ] **Step 6: Verify Task 3 behavior and no qualification-policy edits**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_github_pr_commands.py \
  tests/unit/test_version_bisect_commands.py \
  tests/unit/test_release_monitor_flow.py
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/github_pr/commands.py tests/unit/test_github_pr_commands.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/github_pr/commands.py
git diff --exit-code f5a83da8a625fdfa27b7cd75ec07c7bced058e1c...HEAD -- \
  src/qualock/qualification src/qualock/run src/qualock/source
```

Expected: tests/static checks PASS; protected-module diff is empty.

- [ ] **Step 7: Commit Task 3**

```bash
git add src/qualock/github_pr/commands.py tests/unit/test_github_pr_commands.py
git commit -m "feat: qualify trusted GitHub upgrade requests"
```

---

### Task 4: Trusted Reporter Publisher and Sticky Comment

**Files:**
- Create: `src/qualock/github_pr/publisher.py`
- Create: `tests/unit/test_github_pr_publisher.py`

**Interfaces:**
- Consumes: `PullRequestContext`, optional `PullRequestReport`, Task 1 bounded readers, trusted `workflow_run` event JSON.
- Produces: `GitHubPublishError`, `ReporterValidationError`, `GitHubPublisher` Protocol, `HttpxGitHubPublisher`, `WorkflowRunIdentity`, `validate_reporter_inputs`, `render_pr_comment`, `publish_pr_report`.
- Reporter owns GitHub writes; producer code never imports or calls these write methods.

- [ ] **Step 1: Write RED tests for workflow-run/artifact binding**

Test exact rejection of:

- workflow name other than `QuaLock PR Qualification`;
- workflow event other than `pull_request_target`;
- producer run ID mismatch;
- repository numeric ID/full-name mismatch;
- trusted `expected_repository` mismatch before any publish call;
- context/report PR number mismatch;
- base/head SHA mismatch;
- context/report schema mismatch or oversize;
- report classification incompatible with context classification.

A valid `not_applicable` context may publish without a report. `upgrade` or `invalid_scope` with missing report must map to GitHub error, never success.

- [ ] **Step 2: Run reporter tests and verify RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_github_pr_publisher.py
```

Expected: missing publisher module/functions.

- [ ] **Step 3: Implement workflow-run identity parsing, a narrow publisher Protocol, and bounded httpx client**

`parse_workflow_run_event(event_path, *, expected_repository)` returns this exact trusted envelope before any write:

```python
@dataclass(frozen=True)
class WorkflowRunIdentity:
    workflow_name: str
    event: str
    run_id: int
    repository_id: int
    repository_full_name: str
    trusted_head_sha: str
```

It requires repository full name to equal `expected_repository`, workflow name `QuaLock PR Qualification`, event `pull_request_target`, positive run/repository IDs, and a 40-hex `trusted_head_sha`. `validate_reporter_inputs` then requires `identity.run_id == context.producer_run_id`, repository ID/name equality, `identity.trusted_head_sha == context.base_sha`, report/context PR+base+head+classification equality, and `report.qualock_version == qualock.__version__` when a report exists.

```python
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
    def list_issue_comments(self, repository: str, pr_number: int) -> tuple[IssueComment, ...]: ...
    def create_issue_comment(self, repository: str, pr_number: int, body: str) -> None: ...
    def update_issue_comment(self, repository: str, comment_id: int, body: str) -> None: ...
```

`HttpxGitHubPublisher(token: str, *, client: httpx.Client | None = None)` accepts an injected client only for tests; production creates a client with the same fixed REST version, `follow_redirects=False`, and 15-second timeout, explicitly paginates comments, caps each JSON response at 2097152 bytes before parsing, rejects redirect/non-2xx responses via `GitHubPublishError`, and never logs auth headers or response bodies.

- [ ] **Step 4: Write RED tests for exact status mapping and stale-run behavior**

Define these local test helpers in `tests/unit/test_github_pr_publisher.py`: `context_fixture()` returns one valid strict context; `report_fixture(context, verdict=...)` copies repository/PR/base/head/run/classification identity from that context into a strict report; `workflow_run_fixture(tmp_path, context)` writes a matching workflow-run event JSON and returns its `Path`; `RecordingPublisher` implements the `GitHubPublisher` Protocol and records statuses/comments in lists without network calls.

```python
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
    context = context_fixture()
    report = report_fixture(context, verdict=verdict)
    publisher = RecordingPublisher(current_head=report.head_sha)
    event_path = workflow_run_fixture(tmp_path, context)
    publish_pr_report(
        event_path,
        context,
        report,
        publisher=publisher,
        display_names={},
        expected_repository="owner/repo",
    )
    assert publisher.statuses[-1].state == state
    assert publisher.statuses[-1].context == "qualock/pr"
```

Also prove:

- status context is exactly `qualock/pr`;
- target URL is constructed only from validated `repository_full_name` + `producer_run_id`;
- status is written to validated artifact head SHA, even when that SHA is now stale;
- stale run never creates/updates a comment;
- current-head upgrade creates one marker comment when none exists;
- rerun updates the newest `github-actions[bot]` marker comment;
- a human-authored copied marker is ignored;
- not-applicable never comments;
- every fixed status description is at most 140 characters and contains no PR-supplied text;
- GitHub API write failure propagates.

- [ ] **Step 5: Implement fixed comment rendering and publication order**

Use marker:

```python
_COMMENT_MARKER = "<!-- qualock-pr-report:v1 -->"
_STATUS_CONTEXT = "qualock/pr"
```

Public descriptions/recommendations come from fixed maps keyed by `PrReportVerdict`/`PrReasonCode`; never include raw exception messages or PR text. Comment can resolve trusted canary display names from the reporter's trusted checkout, but falls back to sanitized canary IDs.

`publish_pr_report(event_path, context, report, *, publisher, display_names, expected_repository)` order:

1. require workflow-run repository full name == trusted `expected_repository`, then validate workflow-run identity + artifacts;
2. derive final report outcome (`not_applicable` context-only is success; missing upgrade/invalid-scope report is synthetic reporter-error state);
3. write commit status to `context.head_sha`;
4. fetch live PR head;
5. if live head differs, return without comment mutation;
6. if `not_applicable`, return;
7. find newest marker comment authored exactly by `github-actions[bot]` and update it, otherwise create one.

- [ ] **Step 6: Verify Task 4**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_github_pr_publisher.py tests/unit/test_github_pr_report.py
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/github_pr/publisher.py tests/unit/test_github_pr_publisher.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/github_pr/publisher.py
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/qualock/github_pr/publisher.py tests/unit/test_github_pr_publisher.py
git commit -m "feat: publish trusted GitHub PR qualification status"
```
---

### Task 5: Secure Workflow Templates and Local-Only Setup

**Files:**
- Create: `src/qualock/github_pr/templates.py`
- Create: `src/qualock/github_pr/setup.py`
- Create: `tests/unit/test_github_pr_templates.py`
- Create: `tests/unit/test_github_pr_setup.py`

**Interfaces:**
- Consumes: fixed hidden CLI names defined in Task 6 (`prepare-pr`, `qualify-pr`, `report-pr`) and the Task 1 artifact names/paths.
- Produces: `PRODUCER_WORKFLOW`, `REPORTER_WORKFLOW`, `GitHubSetupConflictError`, `GitHubSetupStatus`, `GitHubSetupOutcome`, `install_github_workflows(root)`.
- Generated files: `.github/workflows/qualock-pr.yml` and `.github/workflows/qualock-pr-report.yml` only.

- [ ] **Step 1: Write RED structural tests for both YAML templates**

Parse with `yaml.load(text, Loader=yaml.BaseLoader)` so YAML 1.1 does not coerce the key `on` to boolean. Assert exact trigger/permissions/action pins plus forbidden patterns.

```python
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


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
```

Structural producer assertions:

- workflow name exactly `QuaLock PR Qualification`;
- `pull_request_target` activity types exactly `opened`, `reopened`, `synchronize`, `ready_for_review`;
- permissions are only `contents: read`, `pull-requests: read`;
- concurrency group includes only repository + PR number and `cancel-in-progress: true`;
- checkout ref exactly `${{ github.event.pull_request.base.sha }}` and `persist-credentials: false`;
- `qualock github prepare-pr` runs before any `QUALOCK_CODEX_AUTH_B64` reference;
- context artifact is named `qualock-pr-context`;
- report artifact is named `qualock-pr-report` and upload step has `if: always()`;
- cleanup of `$HOME/.codex/auth.json` has `if: always()`.

Structural reporter assertions:

- workflow name `QuaLock PR Reporter`;
- `workflow_run.workflows` exactly `["QuaLock PR Qualification"]` and type `completed`;
- permissions exactly `actions: read`, `contents: read`, `statuses: write`, `pull-requests: write`;
- checkout ref exactly `${{ github.event.workflow_run.head_sha }}` and `persist-credentials: false`;
- downloads use `${{ github.event.workflow_run.id }}` and paths under `${{ runner.temp }}`;
- report download is allowed to fail so `report-pr` can convert missing upgrade report to status `error`;
- reporter template contains no `QUALOCK_CODEX_AUTH_B64` string.

Both templates must reject these substrings in normalized text:

```python
for forbidden in (
    "github.event.pull_request.head.sha }}\n          path:",
    "refs/pull/",
    "gh pr checkout",
    "git fetch",
    "pull_request.head.repo",
):
    assert forbidden not in workflow
```

- [ ] **Step 2: Run template tests and verify RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_github_pr_templates.py
```

Expected: missing template module/constants.

- [ ] **Step 3: Implement template constants with exact immutable action pins**

`templates.py` defines:

```python
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"
UPLOAD_ARTIFACT_SHA = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
DOWNLOAD_ARTIFACT_SHA = "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
```

Producer workflow sequence is fixed:

1. checkout trusted base SHA;
2. setup Python 3.12;
3. `python -m pip install .` from trusted checkout;
4. `qualock github prepare-pr --event "$GITHUB_EVENT_PATH" --context-out "$RUNNER_TEMP/pr-context.json" --report-out "$RUNNER_TEMP/pr-report.json"`;
5. upload context artifact immediately;
6. read the already-validated fixed classification enum from context into a step output;
7. only when classification is `upgrade`, materialize `QUALOCK_CODEX_AUTH_B64` to `~/.codex/auth.json` without echoing it, write output `available=true/false`;
8. only when classification is `upgrade`, export the trusted auth-step boolean as `QUALOCK_CREDENTIAL_AVAILABLE` and call `qualock github qualify-pr --context "$RUNNER_TEMP/pr-context.json" --proposed-lock "$RUNNER_TEMP/proposed-baseline.lock" --report-out "$RUNNER_TEMP/pr-report.json" --credential-available "$QUALOCK_CREDENTIAL_AVAILABLE"`;
9. cleanup auth with `if: always()`;
10. upload report with `if: always()` and `if-no-files-found: error`.

`prepare-pr` writes proposed lock bytes for upgrade to `${RUNNER_TEMP}/proposed-baseline.lock`; workflow shell never fetches or interprets PR content itself.

Reporter workflow sequence is fixed:

1. guard that `github.event.workflow_run.event == 'pull_request_target'`;
2. checkout only `github.event.workflow_run.head_sha`;
3. setup Python 3.12 and install trusted package;
4. download required `qualock-pr-context` from the exact triggering run;
5. download `qualock-pr-report` from that run with `continue-on-error: true`;
6. run `qualock github report-pr` with event/context/report paths, where missing report path is accepted and handled fail-closed by Python.

- [ ] **Step 4: Write RED setup tests for idempotence and collision safety**

Required behavior:

```python
def test_setup_creates_exactly_two_workflows(tmp_path: Path) -> None:
    outcome = install_github_workflows(tmp_path)
    assert outcome.status is GitHubSetupStatus.CREATED
    assert (tmp_path / ".github/workflows/qualock-pr.yml").read_text() == PRODUCER_WORKFLOW
    assert (tmp_path / ".github/workflows/qualock-pr-report.yml").read_text() == REPORTER_WORKFLOW


def test_setup_refuses_any_different_existing_file_without_partial_overwrite(tmp_path: Path) -> None:
    producer = tmp_path / ".github/workflows/qualock-pr.yml"
    reporter = tmp_path / ".github/workflows/qualock-pr-report.yml"
    producer.parent.mkdir(parents=True)
    producer.write_text("custom\n")
    reporter.write_text("custom reporter\n")
    with pytest.raises(GitHubSetupConflictError):
        install_github_workflows(tmp_path)
    assert producer.read_text() == "custom\n"
    assert reporter.read_text() == "custom reporter\n"
```

Also verify second run returns `ALREADY_CONFIGURED`, no subprocess is invoked, and no files outside the two approved paths change.

- [ ] **Step 5: Implement preflight-then-write setup logic**

Never write one file before validating both destinations. First classify both target paths as `missing`, `identical`, or `conflict`; any conflict raises without writes. Then create missing files atomically using same-directory temp + `Path.replace`.

```python
class GitHubSetupStatus(str, Enum):
    CREATED = "created"
    ALREADY_CONFIGURED = "already_configured"


@dataclass(frozen=True)
class GitHubSetupOutcome:
    status: GitHubSetupStatus
    producer_path: Path
    reporter_path: Path
```

- [ ] **Step 6: Verify Task 5**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_github_pr_templates.py tests/unit/test_github_pr_setup.py
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/github_pr/templates.py src/qualock/github_pr/setup.py \
  tests/unit/test_github_pr_templates.py tests/unit/test_github_pr_setup.py
/tmp/qualock-static-22-final/bin/mypy --strict \
  src/qualock/github_pr/templates.py src/qualock/github_pr/setup.py
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```bash
git add src/qualock/github_pr/templates.py src/qualock/github_pr/setup.py \
  tests/unit/test_github_pr_templates.py tests/unit/test_github_pr_setup.py
git commit -m "feat: generate secure GitHub qualification workflows"
```

---

### Task 6: GitHub CLI Subgroup and Hidden Workflow Plumbing

**Files:**
- Modify: `src/qualock/cli.py`
- Create: `tests/unit/test_github_pr_cli.py`

**Interfaces:**
- Consumes: `install_github_workflows`, `prepare_pr`, `qualify_prepared_pr`, Task 1 artifact readers/writers, `publish_pr_report`, `HttpxGitHubPrSource`, `HttpxGitHubPublisher`.
- Produces user-visible `qualock github setup`; hidden commands `qualock github prepare-pr`, `qualock github qualify-pr`, `qualock github report-pr`.
- Token boundary: hidden commands read `GITHUB_TOKEN` from environment only; no token option or stdout rendering.

- [ ] **Step 1: Write RED CLI tests for setup and command discovery**

Use `CliRunner` and monkeypatch only Python boundaries, never real GitHub/network calls.

```python
def test_github_setup_is_visible_but_plumbing_commands_are_hidden() -> None:
    result = runner.invoke(cli.app, ["github", "--help"])
    assert result.exit_code == 0
    assert "setup" in result.stdout
    assert "prepare-pr" not in result.stdout
    assert "qualify-pr" not in result.stdout
    assert "report-pr" not in result.stdout
```

Setup output must name both generated paths, `QUALOCK_CODEX_AUTH_B64`, and `qualock/pr`; it must explicitly say the user still needs to commit the workflows, create the secret, and optionally configure branch protection. It must also print this cross-platform encoding example without executing it:

```bash
python -c "import base64,pathlib; p=pathlib.Path.home()/'.codex/auth.json'; print(base64.b64encode(p.read_bytes()).decode())"
```

- [ ] **Step 2: Add RED tests for producer hidden command wiring**

`prepare-pr` options are exact:

```text
--event PATH
--context-out PATH
--report-out PATH
--proposed-lock-out PATH
```

It requires environment variables `GITHUB_TOKEN`, `GITHUB_RUN_ID`, and `GITHUB_REPOSITORY`; builds an `HttpxGitHubPrSource`, calls `prepare_pr`, atomically writes context, writes immediate report for terminal classifications, and writes proposed lock bytes only for upgrade. It prints only the fixed classification enum on stdout for debugging and never token/PR text.

`qualify-pr` options:

```text
--context PATH
--proposed-lock PATH
--report-out PATH
--credential-available true|false
```

It reads bounded artifacts, calls `qualify_prepared_pr`, writes report, and exits 0 for PASS/WARN/BLOCK/INCOMPLETE because the reporter owns GitHub gate publication. Artifact/storage failure exits 1.

- [ ] **Step 3: Add RED tests for reporter hidden command wiring**

`report-pr` options:

```text
--event PATH
--context PATH
--report PATH
```

`--report` may point to a nonexistent file; this is passed as `None` after existence check so publisher can map missing upgrade report to `error`. It requires `GITHUB_TOKEN`, creates `HttpxGitHubPublisher`, loads trusted display names when possible, then calls `publish_pr_report`. Any publisher/API/validation failure exits 1 without printing auth material.

- [ ] **Step 4: Implement thin Typer wiring only**

At module setup:

```python
github_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(github_app, name="github")
```

Decorators:

```python
def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise CommandError(f"required workflow environment is missing: {name}")
    return value


@github_app.command("setup")
def github_setup_command() -> None:
    outcome = install_github_workflows(Path.cwd())
    console.print(f"Producer workflow: {outcome.producer_path}", markup=False)
    console.print(f"Reporter workflow: {outcome.reporter_path}", markup=False)


@github_app.command("prepare-pr", hidden=True)
def github_prepare_pr_command(
    event: Annotated[Path, typer.Option("--event")],
    context_out: Annotated[Path, typer.Option("--context-out")],
    report_out: Annotated[Path, typer.Option("--report-out")],
    proposed_lock_out: Annotated[Path, typer.Option("--proposed-lock-out")],
) -> None:
    source = HttpxGitHubPrSource(token=_required_env("GITHUB_TOKEN"))
    outcome = prepare_pr(
        Path.cwd(),
        event,
        source=source,
        producer_run_id=int(_required_env("GITHUB_RUN_ID")),
        expected_repository=_required_env("GITHUB_REPOSITORY"),
    )
    write_context(context_out, outcome.context)
    if outcome.terminal_report is not None:
        write_report(report_out, outcome.terminal_report)
    if outcome.proposed_lock is not None:
        proposed_lock_out.write_bytes(outcome.proposed_lock)
    console.print(outcome.context.classification.value, markup=False)
```

Continue the same code block with concrete thin bodies:

```python
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
        raise CommandError("--credential-available must be true or false")
    result = qualify_prepared_pr(
        Path.cwd(),
        read_context(context),
        _read_bounded_file(proposed_lock, max_bytes=131_072),
        credential_available=credential_available == "true",
    )
    write_report(report_out, result)


@github_app.command("report-pr", hidden=True)
def github_report_pr_command(
    event: Annotated[Path, typer.Option("--event")],
    context: Annotated[Path, typer.Option("--context")],
    report: Annotated[Path, typer.Option("--report")],
) -> None:
    publisher = HttpxGitHubPublisher(token=_required_env("GITHUB_TOKEN"))
    context_model = read_context(context)
    report_model = read_report(report) if report.is_file() else None
    publish_pr_report(
        event,
        context_model,
        report_model,
        publisher=publisher,
        display_names=_trusted_canary_display_names(Path.cwd()),
        expected_repository=_required_env("GITHUB_REPOSITORY"),
    )
```

`_trusted_canary_display_names` calls trusted `load_project`, returns `{canary.id: canary.name}`, and returns `{}` only for `(ConfigError, CanaryLoadError, FileNotFoundError)` from trusted local config/canary loading; other exceptions propagate. Do not duplicate proposed-lock validation, GitHub HTTP behavior, report rendering, or producer/reporter orchestration inside `cli.py`.

Keep network/orchestration logic out of `cli.py`; commands translate paths/env into package calls and map exceptions to fixed exit codes/messages.

- [ ] **Step 5: Define explicit exit handling and secret-safe messages**

- setup conflict / malformed local arguments -> exit 3;
- missing required GitHub workflow environment -> exit 3;
- source/context/report validation failure -> exit 1 unless producer safely emitted an INCOMPLETE report and returned normally;
- GitHub publish operational failure -> exit 1;
- terminal QuaLock PR verdicts do not directly determine hidden producer command process exit.

Every exception displayed to users is a fixed safe summary such as `GitHub PR context could not be established`; do not print raw `httpx` response bodies or auth headers.

- [ ] **Step 6: Run CLI and integration-focused unit verification**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_github_pr_cli.py \
  tests/unit/test_github_pr_commands.py \
  tests/unit/test_github_pr_publisher.py \
  tests/unit/test_github_pr_templates.py \
  tests/unit/test_github_pr_setup.py
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m qualock.cli --help
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m qualock.cli github --help
/tmp/qualock-static-22-final/bin/ruff check src/qualock/cli.py src/qualock/github_pr tests/unit/test_github_pr_cli.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/cli.py src/qualock/github_pr
```

Expected: `github` appears at top level; only `setup` appears in normal `github --help`; all tests/static checks PASS.

- [ ] **Step 7: Commit Task 6**

```bash
git add src/qualock/cli.py tests/unit/test_github_pr_cli.py
git commit -m "feat: add GitHub PR qualification CLI"
```
---

### Task 7: Low-Tech README Documentation

**Files:**
- Modify: `README.md`
- Do not modify `ROADMAP.md` yet; the design requires moving the roadmap item only after an independent implementation review has approved the code.

**Interfaces:**
- Documents the exact user-visible contract produced by Tasks 5-6; adds no new behavior.

- [ ] **Step 1: Add the setup/adoption flow to README**

Add a focused section near existing release monitoring/version bisect documentation:

```bash
qualock github setup
```

Explain the five user actions in exact order:

1. run setup locally;
2. review and commit `.github/workflows/qualock-pr.yml` and `.github/workflows/qualock-pr-report.yml`;
3. create repository secret `QUALOCK_CODEX_AUTH_B64` from the base64-encoded contents of local `~/.codex/auth.json`;
4. optionally require status context `qualock/pr` in branch protection/rulesets;
5. propose future Codex upgrades as a PR whose only changed path is `.qualock/baseline.lock`.

Document outcomes:

- ordinary PR -> `qualock/pr` success, no comment, no agent qualification cost;
- baseline-only upgrade -> automated qualification + sticky comment;
- baseline plus any other file -> INCOMPLETE/error gate asking for a dedicated upgrade PR;
- PASS -> success, WARN/BLOCK -> failure, INCOMPLETE -> error.

- [ ] **Step 2: Add the mandatory `pull_request_target` warning**

README must explicitly state:

> The generated qualification workflow is safe only because it checks out trusted base code and treats the PR head as data. Do not modify it to checkout or execute PR-controlled code while model credentials are available.

Also state that raw agent transcripts are not uploaded by this feature and `qualock github setup` does not push, create secrets, or modify repository settings.

- [ ] **Step 3: Verify documentation claim discipline**

Search the new section and assert it does not claim:

- arbitrary code PR qualification;
- automatic merge/approval;
- automatic baseline mutation;
- GitHub App/hosted runner support;
- prerelease agent support;
- raw report artifact upload;
- free/low-cost qualification.

Run:

```bash
git diff --check
git diff -- README.md
```

Expected: only accurate Batch #29 user documentation changes.

- [ ] **Step 4: Commit Task 7**

```bash
git add README.md
git commit -m "docs: explain GitHub PR qualification reports"
```

---

### Task 8: Exact-Head Verification, Independent Review, and Delivery Marker

**Files:**
- Verify all Batch #29 files from Tasks 1-7.
- Modify after first clean review only: `ROADMAP.md`.
- Create no release/tag/PyPI artifacts.

**Interfaces:**
- Consumes the complete approved spec and all prior task commits.
- Produces evidence that one exact final HEAD is tested, statically checked, independently reviewed, clean, and safe to offer for later integration.

- [ ] **Step 1: Record the pre-review candidate SHA and require a clean tree**

```bash
git rev-parse HEAD
git status --short
git log --oneline f5a83da8a625fdfa27b7cd75ec07c7bced058e1c..HEAD
```

Expected: one feature branch with only Batch #29 commits and empty status. Save the exact SHA in the Task 8 review package; every following gate must refer to it.

- [ ] **Step 2: Run the full Python verification suite on that exact SHA**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q -rs
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/github_pr src/qualock/cli.py tests/unit/test_github_pr_*.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/github_pr src/qualock/cli.py
/home/pacmap/qualock-exp/.venv/bin/python -m compileall -q src tests
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m qualock.cli --help
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m qualock.cli github --help
git diff --check f5a83da8a625fdfa27b7cd75ec07c7bced058e1c...HEAD
```

Expected: all tests PASS, no unexpected skips, Ruff/mypy/compile PASS, `github` visible, only `setup` public in subgroup help, diff-check clean.

- [ ] **Step 3: Re-run workflow-template security assertions explicitly**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_github_pr_templates.py \
  tests/unit/test_github_pr_source.py \
  tests/unit/test_github_pr_publisher.py
```

Additionally inspect generated YAML and confirm:

- all four action refs are the exact 40-hex pins in Global Constraints;
- producer checkout uses base SHA only;
- reporter checkout uses triggering `workflow_run.head_sha` only;
- no PR-head/merge/fork checkout/fetch command exists;
- reporter has no model secret reference;
- producer has no status/comment write permission;
- artifacts live under `runner.temp` in reporter;
- report upload/credential cleanup use `always()` as required.

- [ ] **Step 4: Prove protected engine/config scope is unchanged**

```bash
git diff --exit-code f5a83da8a625fdfa27b7cd75ec07c7bced058e1c...HEAD -- \
  src/qualock/qualification \
  src/qualock/run \
  src/qualock/source \
  src/qualock/evidence \
  src/qualock/release_monitor \
  src/qualock/version_bisect
git diff --exit-code f5a83da8a625fdfa27b7cd75ec07c7bced058e1c...HEAD -- pyproject.toml
```

Expected: empty diffs. If implementation truly requires a protected seam or dependency change, stop and revise the approved design rather than silently expanding scope.

- [ ] **Step 5: Build an independent review package for the exact candidate SHA**

Include:

- full approved spec;
- this plan's Global Constraints;
- exact base-to-head implementation/test/workflow/README diff;
- full test/static/help evidence;
- changed-file list and protected-path empty-diff evidence.

Ask the reviewer to answer all of these explicitly:

1. Can any PR-controlled code, dependency, branch/ref, path, title/body, or artifact executable content run in a credentialed producer/reporter context?
2. Are ordinary/baseline-only/baseline-plus-other-file classifications exact and fail-closed when changed-file enumeration is incomplete?
3. Does proposed-lock validation bind exact stable candidate version, binary SHA, trusted suite/config/model/canaries/runtime version, while never trusting historical counters as live evidence?
4. Does every real qualification delegate to existing `execute_check` with no policy/evidence fork?
5. Can any raw agent transcript, prompt/task body, stdout/stderr, token, auth data, arbitrary PR text, or source content enter `pr-report.json` or the comment?
6. Are model credential and GitHub write privileges separated across the two workflows with least permissions and full action SHA pins?
7. Does reporter bind context/report to expected producer workflow/event/run/repository/PR/base/head and suppress stale-head comment mutation?
8. Are status semantics exact (`WARN` remains WARN but maps to GitHub failure) and do missing/malformed reports never become success?
9. Is `qualock github setup` local-only, collision-safe, idempotent, and free of push/secret/settings side effects?
10. Are protected qualification/run/source/evidence modules and `pyproject.toml` untouched, and are README claims consistent with V1 non-goals?

Reviewer output must classify findings as Critical / Important / Minor and P1 / P2 and finish with `Approved` or `Changes required`.

- [ ] **Step 6: Resolve review findings before claiming delivery**

For any Critical or Important correctness/security finding:

1. reproduce with a failing test when behavior is involved;
2. make the narrowest code/test fix;
3. commit the fix separately;
4. rerun Steps 1-5 on the new HEAD because the reviewed SHA changed.

Minor/P2 findings may be deferred only when they do not weaken a spec invariant; record the reason in the local execution ledger.

- [ ] **Step 7: Only after the first independent review is Approved, move the ROADMAP item to delivered**

Change `ROADMAP.md` so:

- `GitHub pull-request qualification reports.` is removed from `## Next, only after v0.1 validation`;
- an equivalent delivered bullet is added under `## v0.1 — prove the qualification loop`;
- `Additional coding-agent adapters` and `Smarter canary selection and cost controls` remain in Next.

Commit only the roadmap change:

```bash
git add ROADMAP.md
git commit -m "docs: mark GitHub PR reports delivered"
```

This sequencing prevents the roadmap from claiming delivery before implementation review approval.

- [ ] **Step 8: Rerun final exact-head gates after the ROADMAP commit**

Because HEAD changed, rerun at minimum:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q
/tmp/qualock-static-22-final/bin/ruff check src/qualock/github_pr src/qualock/cli.py tests/unit/test_github_pr_*.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/github_pr src/qualock/cli.py
/home/pacmap/qualock-exp/.venv/bin/python -m compileall -q src tests
git diff --check f5a83da8a625fdfa27b7cd75ec07c7bced058e1c...HEAD
git status --short
```

Expected: all PASS and clean status.

- [ ] **Step 9: Obtain a final focused independent review of the exact final HEAD**

The final reviewer must at least verify:

- the implementation diff is identical to the previously approved implementation except for any approved fixes;
- ROADMAP now accurately reflects delivered behavior and leaves the remaining roadmap items intact;
- all Step 5 security answers remain satisfied;
- no Critical or Important finding exists on the final exact SHA.

If final review changes code, return to Step 8 and review the new SHA again.

- [ ] **Step 10: Final local completion boundary**

Record:

```bash
git rev-parse HEAD
git status --short
git log -1 --oneline
```

Batch #29 is locally complete only if final review says Approved, Critical=0, Important=0, verification is green on that exact SHA, and the tree is clean.

STOP. Do not push, create a PR, merge, tag, create a GitHub Release, or publish to PyPI without a new explicit user authorization at that boundary.

---

## Expected Local Commit Sequence

1. `feat: add sanitized GitHub PR report contracts`
2. `feat: classify GitHub agent upgrade PRs safely`
3. `feat: qualify trusted GitHub upgrade requests`
4. `feat: publish trusted GitHub PR qualification status`
5. `feat: generate secure GitHub qualification workflows`
6. `feat: add GitHub PR qualification CLI`
7. `docs: explain GitHub PR qualification reports`
8. review-driven fix commits only if required
9. `docs: mark GitHub PR reports delivered` only after the first clean independent review

The spec commit `e88ff5e3c1c02104765307c888c87a8f2032ee8d` precedes this sequence. Plan creation/approval is a separate documentation commit and is not implementation.
