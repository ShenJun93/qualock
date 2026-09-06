# Agent-Aware GitHub PR Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the trusted GitHub PR qualification workflow from Codex-only to Codex + Claude Code while keeping the PR head unexecuted, secrets agent-isolated, artifacts sanitized, and Antigravity fail-closed.

**Architecture:** Make PR artifacts carry trusted agent identity, split proposed-lock trust validation from resolver/runtime work, and route exact candidates through a PR-local Codex/Claude resolver allow-list. The producer validates the request before any model secret is exposed, then enters exactly one agent-specific credential/qualification path; the reporter remains credential-free.

**Tech Stack:** Python 3.12+, Typer, Pydantic, packaging.version, GitHub Actions YAML templates, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-06-agent-aware-github-pr-qualification-design.md`

## Global Constraints

- Base commit is `ad0a568e629c29832607d5003315de62edfbe3bc`; implementation branch already contains only the approved spec commits above that base.
- Follow RED → GREEN → focused regression → static checks → commit for every task.
- Trusted base config/baseline selects agent identity before any model secret can be exposed.
- Supported PR agents are exactly `codex` and `claude`; Antigravity has no resolver, credential, app-data, `agy`, or qualification path.
- Proposed PR code is never checked out or executed; existing `pull_request_target` trusted-base checkout remains authoritative.
- `pr-context.json` and `pr-report.json` use schema v2 only; v1 artifacts are rejected, not migrated.
- Sticky comment marker remains exactly `<!-- qualock-pr-report:v1 -->`.
- Claude automation credentials remain direct-auth only: `ANTHROPIC_AUTH_TOKEN` → `ANTHROPIC_API_KEY` → `CLAUDE_CODE_OAUTH_TOKEN`; never read/copy `~/.claude/.credentials.json`.
- Reporter workflow contains no Codex or Claude model-secret references.
- Model-secret values never enter artifacts, `GITHUB_OUTPUT`, argv, report fields, comments, or reason codes.
- Candidate fingerprint in proposed lock must be exact lowercase 64-hex SHA256 and must match the resolved binary together with the resolved agent name.
- Legacy producer migration recognizes only normalized SHA256 `29648454f323b8816f43ccdc4069c00d14c5c720e10fd9a58f4475a5b1c1ce69`; no YAML/whitespace semantic normalization.
- `src/qualock/github_pr/source.py` remains unchanged.
- Do not modify monitor, scheduler, version bisect, Antigravity runtime/resolver/adapter, qualification policy/executor, Docker runner/backend, dependency metadata, or release tooling.
- Do not install dependencies or type stubs.
- Do not run authenticated Claude or Antigravity acceptance tests.
- Known strict-mypy dependency-traversal waiver is limited to the existing missing-PyYAML-stub errors in `src/qualock/canary/loader.py`, `src/qualock/config/io.py`, and `src/qualock/project_setup/config.py`.
- No push, PR, merge, tag, release, or publish action is part of implementation execution.

## File responsibility map

- `github_pr/models.py`: PR-local agent type, schema-v2 context/report, bounded reason codes.
- `github_pr/report.py`: schema-v2 artifact IO and sanitized report construction.
- `github_pr/publisher.py`: reporter binding plus bounded agent label rendering.
- `github_pr/commands.py`: trusted agent preflight, pure proposed-lock validation, PR resolver allow-list, live qualification orchestration.
- `github_pr/templates.py`: mutually exclusive Codex/Claude producer credential paths; reporter remains secret-free.
- `github_pr/setup.py`: exact legacy-template migration and idempotent workflow installation.
- `cli.py` and `README.md`: setup guidance and supported-agent documentation only.

---
### Task 1: Bind Trusted Agent Identity Into PR Artifacts and Reporter

**Files:**
- Modify: `src/qualock/github_pr/models.py`
- Modify: `src/qualock/github_pr/report.py`
- Modify: `src/qualock/github_pr/publisher.py`
- Test: `tests/unit/test_github_pr_models.py`
- Test: `tests/unit/test_github_pr_report.py`
- Test: `tests/unit/test_github_pr_publisher.py`

**Interfaces:**
- Produces: `PrAgent = Literal["codex", "claude"]` in `github_pr.models`.
- Produces: `PrReasonCode.UNSUPPORTED_AGENT`.
- Produces: schema-v2 `PullRequestContext.agent: PrAgent | None = None` and `PullRequestReport.agent: PrAgent | None = None`.
- Produces: all report constructors copy `context.agent`.
- Produces: reporter invariant `report.agent == context.agent`.
- Produces: bounded display mapping `codex -> Codex`, `claude -> Claude Code` for supported upgrade comments.
- Preserves: `_COMMENT_MARKER = "<!-- qualock-pr-report:v1 -->"` and all existing status/SHA/run/repository bindings.

- [ ] **Step 1: Write RED model tests for schema v2 and bounded agent values**

Add tests that construct context/report with `agent="codex"`, `agent="claude"`, and `agent=None`; assert serialized `schema_version == 2`. Add JSON-read tests proving `schema_version: 1` is rejected and invalid agent values such as `"antigravity"` are rejected by Pydantic.
- [ ] **Step 2: Run Task 1 model RED tests**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_github_pr_models.py \
  tests/unit/test_github_pr_report.py -q
```

Expected: failures because schema version is still 1, `agent` is absent, and v1 artifacts are still accepted.

- [ ] **Step 3: Implement the minimal schema-v2 model/report changes**

Use these exact model shapes:

```python
PrAgent = Literal["codex", "claude"]

class PullRequestContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[2] = 2
    # existing fields unchanged
    classification: PrClassification
    agent: PrAgent | None = None

class PullRequestReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[2] = 2
    # existing fields unchanged
    agent: PrAgent | None = None
```

Add `UNSUPPORTED_AGENT = "unsupported_agent"` to `PrReasonCode`. In `report_from_qualification`, `not_applicable_report`, and `incomplete_report`, pass `agent=context.agent` explicitly.
- [ ] **Step 4: Write RED reporter-binding and agent-label tests**

Add publisher tests that:

```python
context = sample_context(agent="claude")
report = sample_report(agent="codex")
with pytest.raises(ReporterValidationError):
    validate_reporter_inputs(identity, context, report)
```

Also assert a valid Claude report comment contains `Claude Code`, a Codex report comment contains `Codex`, and `_COMMENT_MARKER` remains exactly `<!-- qualock-pr-report:v1 -->`.

- [ ] **Step 5: Implement reporter binding and bounded display mapping**

In `validate_reporter_inputs()`, add:

```python
if report.agent != context.agent:
    raise ReporterValidationError("report agent does not match context")
```

In `publisher.py`, keep agent rendering local and bounded:

```python
_AGENT_DISPLAY_NAMES: dict[PrAgent, str] = {
    "codex": "Codex",
    "claude": "Claude Code",
}
```

For supported upgrade reports with `report.agent is not None`, render one sanitized `- Agent: <display>` line. Do not derive the label from proposed-lock text or arbitrary environment values.
- [ ] **Step 6: Run Task 1 GREEN and regressions**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_github_pr_models.py \
  tests/unit/test_github_pr_report.py \
  tests/unit/test_github_pr_publisher.py -q
/home/pacmap/qualock-easy/.venv/bin/ruff check \
  src/qualock/github_pr/models.py \
  src/qualock/github_pr/report.py \
  src/qualock/github_pr/publisher.py \
  tests/unit/test_github_pr_models.py \
  tests/unit/test_github_pr_report.py \
  tests/unit/test_github_pr_publisher.py
git diff --check
```

Expected: focused tests PASS; Ruff and diff-check PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add src/qualock/github_pr/models.py src/qualock/github_pr/report.py \
  src/qualock/github_pr/publisher.py tests/unit/test_github_pr_models.py \
  tests/unit/test_github_pr_report.py tests/unit/test_github_pr_publisher.py
git commit -m "feat: bind agent identity in PR artifacts"
```

---
### Task 2: Establish Trusted Agent and Pure-Validate Proposed Locks Before Secrets

**Files:**
- Modify: `src/qualock/github_pr/commands.py`
- Test: `tests/unit/test_github_pr_commands.py`

**Interfaces:**
- Consumes: `PrAgent`, schema-v2 context/report, `PrReasonCode.UNSUPPORTED_AGENT` from Task 1.
- Produces: `CandidateRequest(agent_name: PrAgent, version: str, binary_sha256: str)`.
- Produces: private `_trusted_pr_agent(root: Path) -> PrAgent` that loads trusted config/baseline, checks freshness and config/baseline agent equality, and accepts only Codex/Claude.
- Produces: `validate_proposed_lock(root: Path, raw: bytes) -> CandidateRequest` with no resolver parameter and no network/runtime work.
- Produces: `prepare_pr()` terminalizes stale/mismatched/unsupported/invalid requests before returning proposed-lock bytes.
- Preserves: `source.py` unchanged; proposed lock is still fetched only through the GitHub source at exact head SHA after trusted agent is established.

- [ ] **Step 1: Write RED trusted-agent preflight tests**

Add focused tests proving:

```python
assert _trusted_pr_agent(codex_root) == "codex"
assert _trusted_pr_agent(claude_root) == "claude"
```

For config/baseline mismatch and stale fingerprints, assert `BaselineStaleError` before any proposed-head read. For Antigravity trusted state, assert a dedicated PR validation/capability error before proposed-head read and before any resolver/check seam can run.
- [ ] **Step 2: Write RED pure-validation tests**

Cover valid Codex and Claude locks plus all current trust invariants. Add exact assertions that:

```python
candidate = validate_proposed_lock(root, raw)
assert candidate == CandidateRequest(
    agent_name="claude",
    version="2.1.263",
    binary_sha256="a" * 64,
)
```

Reject cross-agent locks, `latest`, prerelease/build versions, equal/downgrade versions, wrong suite/config/model/canary/QuaLock/timestamp/counters, and SHA strings that are not exactly lowercase 64-hex. Remove resolver expectations from validation tests and prove the function signature has no `resolver` parameter.

- [ ] **Step 3: Run Task 2 RED tests**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_github_pr_commands.py -q
```

Expected: failures because validation still hard-codes Codex and resolves candidates, `CandidateRequest` lacks agent identity, and prepare does not establish trusted agent before reading the proposed lock.

- [ ] **Step 4: Implement trusted-agent preflight and pure validation**

Add:

```python
class UnsupportedPrAgentError(ValueError):
    pass

@dataclass(frozen=True)
class CandidateRequest:
    agent_name: PrAgent
    version: str
    binary_sha256: str
```
Implement `_trusted_pr_agent(root)` by loading the trusted project and baseline, recomputing suite/config fingerprints, calling `assert_suite_fresh`, then requiring config and baseline agent names to match. Return only `"codex"` or `"claude"`; otherwise raise `UnsupportedPrAgentError`.

Make `validate_proposed_lock(root, raw)` call `_trusted_pr_agent(root)` and perform only local trust checks. Add:

```python
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
```

Require `proposed.agent.name == trusted_agent`; validate the exact stable version, strictly-newer ordering, suite/config/model/canary/QuaLock/timestamp/counter invariants, and `_SHA256_RE.fullmatch(proposed.agent.binary_sha256)`. Return `CandidateRequest(...)` without constructing or calling any resolver.

- [ ] **Step 5: Write RED prepare-ordering tests**

Add tests for `prepare_pr()` showing:

- Codex success returns context agent `codex` and proposed bytes.
- Claude success returns context agent `claude` and proposed bytes.
- config/baseline mismatch or stale fingerprints return terminal INCOMPLETE with `TRUSTED_BASELINE_STALE`, `agent=None`, and zero source `read_file_at_ref` calls.
- Antigravity returns terminal INCOMPLETE with `UNSUPPORTED_AGENT`, `agent=None`, and zero proposed-head reads.
- invalid/cross-agent proposed lock returns terminal `INVALID_PROPOSED_LOCK`, preserves the established supported context agent, and returns no proposed bytes.
- proposed-lock GitHub read failure after trusted agent establishment returns `INVALID_PROPOSED_LOCK` while preserving that context agent.
- NOT_APPLICABLE and INVALID_SCOPE keep `agent=None` and do not invoke `_trusted_pr_agent`.

- [ ] **Step 6: Implement prepare-stage terminalization**

For `UPGRADE`, call `_trusted_pr_agent()` before `source.read_file_at_ref()`, update the immutable context via `context.model_copy(update={"agent": agent})`, then fetch and pure-validate the proposed lock. Catch stale/mismatch, unsupported agent, source failure, and validation failure into the bounded report reasons specified above. Return proposed bytes only after pure validation succeeds.
- [ ] **Step 7: Run Task 2 GREEN and static checks**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_github_pr_commands.py -q
/home/pacmap/qualock-easy/.venv/bin/ruff check \
  src/qualock/github_pr/commands.py tests/unit/test_github_pr_commands.py
git diff --check
```

Expected: command tests PASS; validation tests prove no resolver call/signature remains; Ruff and diff-check PASS.

- [ ] **Step 8: Commit Task 2**

```bash
git add src/qualock/github_pr/commands.py tests/unit/test_github_pr_commands.py
git commit -m "feat: trust agent before PR qualification"
```

---
### Task 3: Route Live Qualification Through a Codex/Claude PR Resolver Allow-List

**Files:**
- Modify: `src/qualock/github_pr/commands.py`
- Test: `tests/unit/test_github_pr_commands.py`

**Interfaces:**
- Consumes: `CandidateRequest(agent_name, version, binary_sha256)` and `_trusted_pr_agent()` from Task 2.
- Produces: `_default_pr_resolver(agent_name: PrAgent) -> Resolver` using the standard QuaLock agent cache root.
- Produces: Codex -> `CodexResolver`, Claude -> `ClaudeResolver`; no Antigravity resolver import/construction/fallback.
- Produces: resolved `AgentBinary.name` and `.sha256` must both match the trusted candidate request before `execute_check()`.
- Produces: exact candidate spec `f"{candidate.agent_name}@{candidate.version}"` and passes the same resolver object to `execute_check()`.
- Preserves: `CheckExecutor(root, candidate_spec, *, resolver=...)` seam and existing PASS/WARN/BLOCK/INCOMPLETE report projection.

- [ ] **Step 1: Write RED resolver-factory tests**

Monkeypatch constructor classes and cache-root lookup, then assert:

```python
assert isinstance(_default_pr_resolver("codex"), CodexResolver)
assert isinstance(_default_pr_resolver("claude"), ClaudeResolver)
```

Also use static/source assertions proving `github_pr.commands` does not import or reference `AntigravityResolver`, `AntigravityAdapter`, or `agy`.

- [ ] **Step 2: Write RED candidate-identity and routing tests**

Use fake resolvers returning `AgentBinary` objects. Cover:

- valid Codex binary -> exactly one `codex@<version>` check call;
- valid Claude binary -> exactly one `claude@<version>` check call;
- wrong `AgentBinary.name` -> `INVALID_PROPOSED_LOCK`, no check;
- SHA mismatch -> `INVALID_PROPOSED_LOCK`, no check;
- resolver exception -> `QUALIFICATION_FAILED`;
- check exception -> `QUALIFICATION_FAILED`;
- missing credential -> `CREDENTIAL_UNAVAILABLE`, no resolver/check after pure validation;
- malformed/cross-agent proposed lock with missing credential still reports `INVALID_PROPOSED_LOCK` because pure validation precedes credential gating.
- [ ] **Step 3: Run Task 3 RED tests**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_github_pr_commands.py -q
```

Expected: Claude factory/routing and wrong-agent binary checks fail because the current PR resolver is Codex-only and qualification still hard-codes `codex@...`.

- [ ] **Step 4: Implement the PR-local resolver factory**

Import `ClaudeResolver` and the shared `default_agent_cache_root()` helper, then implement:

```python
def _default_pr_resolver(agent_name: PrAgent) -> Resolver:
    cache = default_agent_cache_root()
    if agent_name == "codex":
        return CodexResolver(cache)
    if agent_name == "claude":
        return ClaudeResolver(cache)
    raise UnsupportedPrAgentError(f"unsupported PR qualification agent: {agent_name}")
```

Do not import the generic command resolver factory because it can construct Antigravity.

- [ ] **Step 5: Implement exact binary identity verification and agent-aware check routing**

`qualify_prepared_pr()` must:

1. call `validate_proposed_lock(root, proposed_lock)`;
2. require `context.agent == candidate.agent_name` and reject `None`/mismatch as invalid proposed-lock input;
3. return `CREDENTIAL_UNAVAILABLE` before resolver construction when the request is otherwise valid and credential is unavailable;
4. construct or use the injected resolver;
5. `resolve(candidate.version)` once;
6. require `resolved.name == candidate.agent_name` and `resolved.sha256 == candidate.binary_sha256`;
7. call `check_executor(root, f"{candidate.agent_name}@{candidate.version}", resolver=resolver)` exactly once.
Keep exception mapping ordered and sanitized:

```python
except BaselineStaleError:
    return incomplete_report(context, reason_codes=(PrReasonCode.TRUSTED_BASELINE_STALE,))
except UnsupportedPrAgentError:
    return incomplete_report(context, reason_codes=(PrReasonCode.UNSUPPORTED_AGENT,))
except PrValidationError:
    return incomplete_report(context, reason_codes=(PrReasonCode.INVALID_PROPOSED_LOCK,))
except Exception:  # producer boundary
    return incomplete_report(context, reason_codes=(PrReasonCode.QUALIFICATION_FAILED,))
```

Convert wrong resolved agent/SHA into `PrValidationError` inside the try block so it follows the invalid-proposed-lock path.

- [ ] **Step 6: Run Task 3 GREEN and Codex regression tests**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_github_pr_commands.py \
  tests/unit/test_commands.py \
  tests/unit/test_claude_resolver.py -q
/home/pacmap/qualock-easy/.venv/bin/ruff check \
  src/qualock/github_pr/commands.py tests/unit/test_github_pr_commands.py
git diff --check
```

Expected: PR command tests and existing agent-command/Claude-resolver regressions PASS; no authenticated agent invocation occurs.

- [ ] **Step 7: Commit Task 3**

```bash
git add src/qualock/github_pr/commands.py tests/unit/test_github_pr_commands.py
git commit -m "feat: route PR qualification by trusted agent"
```

---
### Task 4: Make the Producer Workflow Agent-Aware Without Mixing Secrets

**Files:**
- Modify: `src/qualock/github_pr/templates.py`
- Test: `tests/unit/test_github_pr_templates.py`

**Interfaces:**
- Consumes: prepare stage now writes schema-v2 context, terminal report on invalid requests, and proposed-lock file only after pure validation.
- Produces: producer step `id: plan` with bounded outputs `classification`, `agent`, and `ready`.
- Produces: Codex path gated by `ready == true && agent == codex`.
- Produces: Claude path gated by `ready == true && agent == claude`.
- Preserves: trusted-base checkout, pinned action SHAs, producer permissions, reporter workflow permissions/triggers, context/report artifact names, `if: always()` report upload.

- [ ] **Step 1: Write RED structural tests for the qualification-plan step**

Parse `PRODUCER_WORKFLOW` and assert the plan step occurs after `qualock github prepare-pr` and before every model-secret reference. Assert it reads only the fixed context/report/proposed-lock paths and emits exactly the bounded keys `classification`, `agent`, and `ready`.

The `ready` computation must be equivalent to:

```python
ready = (
    classification == "upgrade"
    and agent in {"codex", "claude"}
    and Path(proposed_lock_path).is_file()
    and not Path(report_path).is_file()
)
```

Add tests showing the workflow conditions for both live qualification paths require `steps.plan.outputs.ready == 'true'` and the exact matching agent.

- [ ] **Step 2: Write RED secret-isolation tests**

Structurally assert:

- `QUALOCK_CODEX_AUTH_B64` appears only in the Codex credential step.
- `QUALOCK_ANTHROPIC_AUTH_TOKEN`, `QUALOCK_ANTHROPIC_API_KEY`, and `QUALOCK_CLAUDE_CODE_OAUTH_TOKEN` appear only in the Claude qualification step.
- the reporter template contains none of those four repository secret names.
- Codex and Claude qualification conditions are mutually exclusive.
- unsupported/null agent enters neither secret-bearing path.
- [ ] **Step 3: Run Task 4 RED tests**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_github_pr_templates.py -q
```

Expected: failures because the producer has only the current Codex credential path and no trusted `plan` outputs or Claude secret path.

- [ ] **Step 4: Implement the bounded workflow planning step**

Replace the current classification-only step with a fixed-code Python step that reads the trusted artifact and fixed file paths. Use environment variables only for those runner-temp paths; do not interpolate PR-controlled values into shell source.

The script body must have this substance:

```python
with open(os.environ["QUALOCK_CONTEXT"], encoding="utf-8") as handle:
    context = json.load(handle)
classification = context["classification"]
agent = context.get("agent") or ""
ready = (
    classification == "upgrade"
    and agent in {"codex", "claude"}
    and Path(os.environ["QUALOCK_PROPOSED_LOCK"]).is_file()
    and not Path(os.environ["QUALOCK_REPORT"]).is_file()
)
with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
    output.write(f"classification={classification}\n")
    output.write(f"agent={agent}\n")
    output.write(f"ready={'true' if ready else 'false'}\n")
```
- [ ] **Step 5: Implement mutually exclusive Codex and Claude qualification paths**

Keep the Codex materialization semantics, but gate both materialization and live Codex qualification with:

```text
steps.plan.outputs.ready == 'true' && steps.plan.outputs.agent == 'codex'
```

Keep `set +x`, `install -d -m 700 "$HOME/.codex"`, base64 decode to `$HOME/.codex/auth.json`, `chmod 600`, boolean availability output, and scoped cleanup using `rm -f "$HOME/.codex/auth.json"`.

Add a separate Claude qualification step gated by:

```text
steps.plan.outputs.ready == 'true' && steps.plan.outputs.agent == 'claude'
```

Map repository secrets to runtime env names only in that step:

```yaml
ANTHROPIC_AUTH_TOKEN: ${{ secrets.QUALOCK_ANTHROPIC_AUTH_TOKEN }}
ANTHROPIC_API_KEY: ${{ secrets.QUALOCK_ANTHROPIC_API_KEY }}
CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.QUALOCK_CLAUDE_CODE_OAUTH_TOKEN }}
```

Inside the Claude shell, use `set +x`, determine only the boolean `credential_available`, enforce precedence by unsetting lower-priority variables, unset all three when none is non-empty, then call the existing hidden `qualock github qualify-pr` command with that boolean. Never echo a secret value or write one to `GITHUB_OUTPUT`.
- [ ] **Step 6: Run Task 4 GREEN plus workflow security regressions**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_github_pr_templates.py -q
/home/pacmap/qualock-easy/.venv/bin/ruff check \
  src/qualock/github_pr/templates.py tests/unit/test_github_pr_templates.py
git diff --check
```

Also run a source assertion that `REPORTER_WORKFLOW` contains none of:

```text
QUALOCK_CODEX_AUTH_B64
QUALOCK_ANTHROPIC_AUTH_TOKEN
QUALOCK_ANTHROPIC_API_KEY
QUALOCK_CLAUDE_CODE_OAUTH_TOKEN
```

Expected: all workflow tests and static assertions PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/qualock/github_pr/templates.py tests/unit/test_github_pr_templates.py
git commit -m "feat: isolate PR credentials by agent"
```

---
### Task 5: Safely Migrate the Exact Legacy Producer Template

**Files:**
- Modify: `src/qualock/github_pr/setup.py`
- Test: `tests/unit/test_github_pr_setup.py`

**Interfaces:**
- Consumes: new `PRODUCER_WORKFLOW` from Task 4 and unchanged `REPORTER_WORKFLOW`.
- Produces: `GitHubSetupStatus.UPGRADED = "upgraded"`.
- Produces: exact legacy-producer detection using normalized text SHA256 `29648454f323b8816f43ccdc4069c00d14c5c720e10fd9a58f4475a5b1c1ce69`.
- Preserves: all conflict classification happens before writes; user-customized producer/reporter files are never overwritten.
- Preserves: atomic temporary-file/replace writes and idempotent second run.

- [ ] **Step 1: Write RED migration-status and exact-hash tests**

Add tests that materialize the known pre-Batch-#39 producer text and assert:

```python
outcome = install_github_workflows(tmp_path)
assert outcome.status is GitHubSetupStatus.UPGRADED
assert producer_path.read_text(encoding="utf-8") == PRODUCER_WORKFLOW
```

The fixture must use the exact legacy producer bytes/text represented by the locked normalized SHA, not a reconstructed semantically-equivalent YAML document.

- [ ] **Step 2: Write RED conflict and atomicity tests**

Cover:

- one-byte/whitespace modification of legacy producer -> conflict and byte-identical file remains;
- exact legacy producer + conflicting reporter -> conflict and producer is not migrated;
- exact legacy producer + missing reporter -> producer migrates and reporter is created; status is `UPGRADED`;
- missing producer + exact current reporter -> status `CREATED`;
- exact current producer/reporter -> `ALREADY_CONFIGURED`;
- migration followed by second setup -> `ALREADY_CONFIGURED`;
- CRLF legacy text is eligible only through the same `Path.read_text()` universal-newline normalization, with no extra whitespace/YAML normalization.
- [ ] **Step 3: Run Task 5 RED tests**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_github_pr_setup.py -q
```

Expected: migration cases fail because current setup treats every non-current producer as conflict and has no `UPGRADED` status.

- [ ] **Step 4: Implement exact legacy classification**

Add:

```python
import hashlib

_LEGACY_PRODUCER_SHA256 = (
    "29648454f323b8816f43ccdc4069c00d14c5c720e10fd9a58f4475a5b1c1ce69"
)

def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

Add a producer-specific classifier that reads text through `Path.read_text(encoding="utf-8")`, returns `missing`, `identical`, `legacy`, or `conflict`, and treats `legacy` only when `_text_sha256(text)` equals the exact constant above. Do not parse YAML or normalize whitespace.

- [ ] **Step 5: Implement conflict-before-write migration semantics**

Add `UPGRADED` to `GitHubSetupStatus`. Compute producer and reporter states first; if either is conflict, raise before writing anything. Then:

```python
if producer_state in {"missing", "legacy"}:
    _write_atomic(producer_path, PRODUCER_WORKFLOW)
if reporter_state == "missing":
    _write_atomic(reporter_path, REPORTER_WORKFLOW)

status = (
    GitHubSetupStatus.UPGRADED
    if producer_state == "legacy"
    else GitHubSetupStatus.CREATED
    if "missing" in {producer_state, reporter_state}
    else GitHubSetupStatus.ALREADY_CONFIGURED
)
```
- [ ] **Step 6: Run Task 5 GREEN and idempotence regressions**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_github_pr_setup.py -q
/home/pacmap/qualock-easy/.venv/bin/ruff check \
  src/qualock/github_pr/setup.py tests/unit/test_github_pr_setup.py
git diff --check
```

Expected: creation, migration, conflict, atomicity, and second-run tests PASS.

- [ ] **Step 7: Commit Task 5**

```bash
git add src/qualock/github_pr/setup.py tests/unit/test_github_pr_setup.py
git commit -m "feat: migrate trusted PR workflow template"
```

---
### Task 6: Update Setup UX and GitHub PR Documentation for Codex + Claude

**Files:**
- Modify: `src/qualock/cli.py`
- Modify: `README.md`
- Test: `tests/unit/test_github_pr_cli.py`

**Interfaces:**
- Consumes: `GitHubSetupStatus.UPGRADED` but does not need to expose or mutate any secret value.
- Produces: setup instructions for Codex secret `QUALOCK_CODEX_AUTH_B64` and Claude secret names `QUALOCK_ANTHROPIC_AUTH_TOKEN`, `QUALOCK_ANTHROPIC_API_KEY`, `QUALOCK_CLAUDE_CODE_OAUTH_TOKEN`.
- Produces: `claude setup-token` guidance for subscription automation.
- Preserves: existing Codex base64 recipe, workflow paths, `qualock/pr` branch-protection guidance, and hidden command behavior.
- Documents: trusted base agent selects credential path; Antigravity PR qualification remains unsupported.

- [ ] **Step 1: Write RED setup-output tests**

Keep the existing Codex setup-output test unchanged and add a separate test named test_github_setup_prints_claude_secret_guidance_without_values. Require all of:

```text
QUALOCK_CODEX_AUTH_B64
QUALOCK_ANTHROPIC_AUTH_TOKEN
QUALOCK_ANTHROPIC_API_KEY
QUALOCK_CLAUDE_CODE_OAUTH_TOKEN
claude setup-token
qualock/pr
```

Keep the exact existing Codex base64 recipe assertion.

Add a security regression that places sentinel values in the three Claude runtime env variables and asserts none of those sentinel values appears in setup stdout/stderr. The setup command must not call `_required_env` for any model credential.

- [ ] **Step 2: Run Task 6 CLI RED tests**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_github_pr_cli.py -q
```

Expected: Claude setup-guidance assertions fail while existing Codex setup assertions remain green.
- [ ] **Step 3: Implement setup guidance without reading secrets**

Keep the current Codex instructions and add a separate Claude section stating that users should configure one supported repository secret where practical:

```text
QUALOCK_ANTHROPIC_AUTH_TOKEN
QUALOCK_ANTHROPIC_API_KEY
QUALOCK_CLAUDE_CODE_OAUTH_TOKEN
```

Explain that subscription automation can obtain `CLAUDE_CODE_OAUTH_TOKEN` with `claude setup-token`. Do not inspect environment variables, local Claude credential files, or repository secret values.

- [ ] **Step 4: Update README GitHub PR qualification section**

Change the current Codex-only adoption text so it states:

- trusted base agent selects Codex or Claude credential path;
- Codex uses the existing base64 auth-file repository secret;
- Claude uses one of the three direct-auth repository secrets;
- baseline-only upgrade PRs qualify exact stable releases for the trusted Codex/Claude agent;
- Antigravity remains unsupported for GitHub PR qualification;
- ordinary/invalid-scope behavior, status mapping, trusted-base checkout, no PR-head execution, sanitized artifacts, and human-controlled merge/adoption remain unchanged;
- cost wording is agent-generic rather than Codex-only.

Do not document a GitHub App, automatic secret creation, PR-head checkout, or automatic merge.

- [ ] **Step 5: Run Task 6 GREEN and documentation regressions**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_github_pr_cli.py -q
/home/pacmap/qualock-easy/.venv/bin/ruff check \
  src/qualock/cli.py tests/unit/test_github_pr_cli.py
git diff --check
```

Expected: CLI tests PASS and no secret sentinel is emitted.

- [ ] **Step 6: Commit Task 6**

```bash
git add src/qualock/cli.py README.md tests/unit/test_github_pr_cli.py
git commit -m "docs: explain agent-aware PR qualification"
```

---
### Task 7: Whole-Branch Verification, Security Proofs, and Independent Review

**Files:**
- Verify all Batch #39 files from Tasks 1–6.
- Modify only files required by one bounded final fix wave if review finds a real issue.

**Interfaces:**
- Consumes: all prior task commits and the approved Batch #39 spec.
- Produces: reproducible local verification evidence and an independent whole-branch verdict.
- Produces: local-ready status only when no Critical/Important review finding remains and every required gate passes.
- Preserves: no push/PR/merge/tag/release/publish and no authenticated Claude/Antigravity execution.

- [ ] **Step 1: Require a clean candidate tree and record exact SHA**

Run:

```bash
git status --short --branch
git rev-parse HEAD
git diff --check
```

Expected: worktree clean before final review package creation; record the exact candidate SHA in the SDD/review ledger.

- [ ] **Step 2: Run the focused GitHub PR suite**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/unit/test_github_pr_models.py \
  tests/unit/test_github_pr_source.py \
  tests/unit/test_github_pr_commands.py \
  tests/unit/test_github_pr_report.py \
  tests/unit/test_github_pr_publisher.py \
  tests/unit/test_github_pr_setup.py \
  tests/unit/test_github_pr_templates.py \
  tests/unit/test_github_pr_cli.py -q
```

Expected: all focused tests PASS.
- [ ] **Step 3: Run full suite and Python quality gates**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
/home/pacmap/qualock-easy/.venv/bin/ruff check \
  src/qualock/github_pr/models.py \
  src/qualock/github_pr/commands.py \
  src/qualock/github_pr/report.py \
  src/qualock/github_pr/publisher.py \
  src/qualock/github_pr/setup.py \
  src/qualock/github_pr/templates.py \
  src/qualock/cli.py \
  tests/unit/test_github_pr_models.py \
  tests/unit/test_github_pr_commands.py \
  tests/unit/test_github_pr_report.py \
  tests/unit/test_github_pr_publisher.py \
  tests/unit/test_github_pr_setup.py \
  tests/unit/test_github_pr_templates.py \
  tests/unit/test_github_pr_cli.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q \
  src/qualock/github_pr src/qualock/cli.py
git diff --check
```

Expected: full pytest PASS; Ruff, compileall, and diff-check PASS.

- [ ] **Step 4: Run strict mypy on touched source**

Run:

```bash
/home/pacmap/qualock-easy/.venv/bin/mypy --strict \
  src/qualock/github_pr/models.py \
  src/qualock/github_pr/commands.py \
  src/qualock/github_pr/report.py \
  src/qualock/github_pr/publisher.py \
  src/qualock/github_pr/setup.py \
  src/qualock/github_pr/templates.py \
  src/qualock/cli.py
```

Expected: no new touched-source type error. If dependency traversal exposes errors, only the three pre-existing missing-PyYAML-stub files named in Global Constraints may be waived; record exact output and do not install stubs.
- [ ] **Step 5: Run static scope and Antigravity isolation proofs**

Run:

```bash
BASE=ad0a568e629c29832607d5003315de62edfbe3bc
git diff --quiet "$BASE"...HEAD -- \
  src/qualock/github_pr/source.py \
  src/qualock/commands.py \
  src/qualock/agents \
  src/qualock/baseline \
  src/qualock/config \
  src/qualock/qualification \
  src/qualock/release_monitor \
  src/qualock/scheduler \
  src/qualock/version_bisect \
  src/qualock/run \
  pyproject.toml
```

Expected: every `git diff --quiet` returns 0.

Also assert `src/qualock/github_pr/commands.py` contains none of:

```text
AntigravityResolver
AntigravityAdapter
from_environment()
agy
```

Do not weaken this proof by importing a generic factory that can reach Antigravity indirectly.

- [ ] **Step 6: Run static secret/template proofs**

Run this exact substance with the canonical Python:

```python
from pathlib import Path
from qualock.github_pr.templates import PRODUCER_WORKFLOW, REPORTER_WORKFLOW

secret_names = (
    "QUALOCK_CODEX_AUTH_B64",
    "QUALOCK_ANTHROPIC_AUTH_TOKEN",
    "QUALOCK_ANTHROPIC_API_KEY",
    "QUALOCK_CLAUDE_CODE_OAUTH_TOKEN",
)
assert all(name in PRODUCER_WORKFLOW for name in secret_names)
assert all(name not in REPORTER_WORKFLOW for name in secret_names)

for filename in (
    "src/qualock/github_pr/models.py",
    "src/qualock/github_pr/report.py",
    "src/qualock/github_pr/publisher.py",
):
    source = Path(filename).read_text(encoding="utf-8")
    for forbidden in (*secret_names, ".claude/.credentials.json", "events_jsonl"):
        assert forbidden not in source, (filename, forbidden)
```

Task 4 parsed-YAML tests remain the authority that each producer secret reference is scoped to the intended mutually exclusive step.
- [ ] **Step 7: Build and submit one immutable whole-branch review package**

Create a review package for exactly:

```text
ad0a568e629c29832607d5003315de62edfbe3bc...HEAD
```

Include the approved spec, this implementation plan, commit list, changed-file list, full diff, focused/full test results, Ruff/mypy/compileall/diff-check results, static scope/secret proofs, and any task-level reviewer rulings from the execution ledger.

Dispatch a fresh independent Claude Opus reviewer read-only. Require explicit answers to these gates:

1. Does trusted base state select agent before any model secret exposure?
2. Can proposed lock or PR-controlled data select another credential family?
3. Is pure validation resolver/network/runtime-free and repeated at qualification boundary?
4. Does live routing use only Codex/Claude and verify resolved agent name + SHA before `execute_check()`?
5. Can Antigravity reach any PR credential/resolver/app-data/agent path?
6. Are context/report v2 bindings and reporter SHA/run/repository protections complete?
7. Are Codex/Claude workflow secret paths mutually exclusive and is reporter credential-free?
8. Does setup overwrite only the exact legacy producer and remain conflict-before-write otherwise?
9. Is existing Codex behavior semantically preserved?
10. Is any change outside Batch #39 scope?

Reviewer must return `READY` or `NOT READY` and classify findings Critical / Important / Minor.

- [ ] **Step 8: Apply at most one bounded final fix wave if needed**

If the whole-branch reviewer reports actionable findings, dispatch one fresh implementer for one bounded fix wave. Do not broaden scope. Rerun every Step 2–6 gate after the fix, then request one scoped re-review of only the fix diff plus affected invariants.

Critical/Important findings must be resolved before local-ready. A Minor may be deferred only if the reviewer explicitly agrees it does not weaken a spec/security invariant; record that ruling in the execution ledger. Do not enter repeated fix/review loops.

- [ ] **Step 9: Declare local-ready only from fresh evidence**

Require:

```bash
git status --short --branch
git diff --check
git rev-parse HEAD
```

Expected: clean worktree, final diff-check PASS, exact reviewed HEAD recorded. Report focused/full suite counts, static gates, mypy waiver if applicable, whole-branch verdict, and any residual non-blocking Minor ruling. Do not push or open a PR without separate user authorization.

---
