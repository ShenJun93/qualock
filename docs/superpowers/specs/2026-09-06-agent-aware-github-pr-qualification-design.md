# Batch #39 — Agent-Aware GitHub PR Qualification Design

**Date:** 2026-09-06
**Base:** `main@ad0a568e629c29832607d5003315de62edfbe3bc`
**Status:** In-chat design approved; written spec awaiting user approval.

## Goal

Extend QuaLock's security-sensitive GitHub PR qualification workflow from Codex-only to Codex + Claude Code without weakening the trusted-base model introduced by Batch #29.

A baseline-only PR may request an upgrade for the agent already selected by the trusted base project. QuaLock must derive the agent from trusted base config/baseline state, validate the proposed lock without executing PR code, expose only that agent's automation credential path, resolve the exact candidate through an allow-listed resolver, and delegate the live verdict to the existing `execute_check()` engine.

## Non-goals

- No Antigravity GitHub PR qualification.
- No change to qualification policy, grader policy, canary semantics, Docker/host execution contracts, monitor, scheduler, or version bisect.
- No generic credential-provider refactor across QuaLock.
- No PR-head checkout or execution.
- No migration of user-authored GitHub workflows.
- No authenticated Claude or Antigravity acceptance run in this batch.
- No dependency installation, release, tag, or publishing action.

## Existing state

The current PR subsystem is intentionally Codex-specific in three places:

1. `github_pr.commands.validate_proposed_lock()` requires proposed, trusted baseline, and config agent names all equal `"codex"`, then resolves the candidate inside validation.
2. `qualify_prepared_pr()` always constructs `codex@<version>` and `_default_resolver()` always returns `CodexResolver`.
3. `github_pr.templates.PRODUCER_WORKFLOW` stages only `QUALOCK_CODEX_AUTH_B64` into `~/.codex/auth.json`.

The trusted-source model remains correct and must be preserved: `pull_request_target` checks out the trusted base SHA, PR metadata and the proposed `.qualock/baseline.lock` are read through bounded GitHub API calls at the exact PR head, and the PR head repository is never checked out or executed.

The reporter remains a separate `workflow_run` workflow with no model credentials. It publishes the stable `qualock/pr` status and a sanitized sticky comment after validating repository/run/base/head bindings.

## Architectural decision

Use a capability-first, end-to-end extension rather than a Python-only genericization. The complete feature includes trusted agent derivation, pure proposed-lock validation, Codex/Claude resolver selection, exact candidate identity verification, agent-bound artifacts, and agent-specific workflow credential staging.

A Python-only implementation is rejected because Claude would appear supported in unit tests while the generated GitHub workflow could not authenticate it. A broad global credential abstraction is rejected as unnecessary scope and additional security surface.

## 1. Trusted agent identity

Add a PR-local type:

```python
PrAgent = Literal["codex", "claude"]
```

The trusted agent is derived only from the trusted base checkout. For an `UPGRADE` classification, QuaLock loads trusted config, canaries, and `baseline.lock`, verifies suite/config freshness, then requires `config.agent.name == baseline.agent.name`.

Supported trusted agents are exactly Codex and Claude. Antigravity and any future unknown agent are not coerced or routed through another resolver.

The untrusted proposed lock cannot choose the credential family. Its `agent.name` is validated only after the trusted base agent has already been established.

If trusted config/baseline identity is inconsistent or stale, the producer creates a terminal INCOMPLETE report using `TRUSTED_BASELINE_STALE`. It must not fetch or resolve a candidate, stage an agent credential, or invoke qualification.

If the trusted agent is unsupported, the producer creates a terminal INCOMPLETE report with new bounded reason code `UNSUPPORTED_AGENT`. It must not invoke `agy`, construct an Antigravity resolver/backend, inspect Antigravity app data, or stage any model credential.

## 2. Artifact schema v2

`pr-context.json` and `pr-report.json` are ephemeral run-scoped artifacts, not persisted user state. New producer/reporter runs use schema v2 and do not read/migrate v1 artifacts.

`PullRequestContext` becomes:

```python
class PullRequestContext(BaseModel):
    schema_version: Literal[2] = 2
    # existing identity fields unchanged
    classification: PrClassification
    agent: PrAgent | None = None
```

`agent` is non-null only after trusted-base derivation succeeds for a supported upgrade. Ordinary PRs, invalid-scope PRs, stale/mismatched trusted state, and unsupported-agent terminal outcomes may retain `None`.

`PullRequestReport` also becomes schema v2 and adds `agent: PrAgent | None = None`. Every report constructor copies `context.agent`; live qualification reports therefore carry the same trusted agent identity.

Reporter validation adds the binding invariant `report.agent == context.agent` in addition to the existing repository ID/name, PR number, base SHA, head SHA, classification, producer run ID, and QuaLock-version checks.

The sticky-comment marker remains exactly `<!-- qualock-pr-report:v1 -->`. The marker identifies the comment slot rather than artifact schema, so retaining it prevents duplicate bot comments when users upgrade QuaLock.

## 3. Producer prepare-stage ordering

For `UPGRADE`, the trusted producer order is:

```text
establish PR identity and changed-file classification
→ load/fingerprint trusted base project
→ verify config/baseline agent equality and freshness
→ require trusted agent ∈ {codex, claude}
→ write trusted agent into context
→ fetch proposed baseline.lock from exact PR head via GitHub API
→ pure-validate proposed lock against trusted base state
→ only then mark the run ready for credential staging/qualification
```

Pure validation runs in the prepare stage specifically so malformed, cross-agent, stale, or otherwise invalid upgrade requests never cause a model credential to be exposed to a later workflow step.

`prepare_pr()` returns the proposed lock bytes only when the request passes this no-secret/no-resolver trust gate. Any terminal prepare outcome writes a report and returns no proposed lock.

The workflow's post-prepare planning step reads only the trusted `pr-context.json` plus fixed-path file existence. It emits bounded values `classification`, `agent`, and `ready`, where `ready=true` requires: classification `upgrade`, supported non-null agent, a regular proposed-lock file, and no terminal report already produced by prepare.

Credential and qualification steps require `ready == true` and the matching trusted agent. Unsupported/null agents cannot enter either path.

## 4. Pure proposed-lock trust validation

`validate_proposed_lock()` no longer accepts or calls a resolver. It performs no npm lookup, package install/cache mutation, agent execution, Docker work, credential read, or network access.

Its output becomes:

```python
@dataclass(frozen=True)
class CandidateRequest:
    agent_name: PrAgent
    version: str
    binary_sha256: str
```

Validation requires all of the following:

- proposed agent equals the trusted config/baseline agent;
- candidate version is exact stable `X.Y.Z` with no `latest`, prerelease, or build metadata;
- candidate version is strictly newer than the trusted baseline;
- proposed suite/config fingerprints equal fresh trusted-base fingerprints;
- proposed model pin equals trusted config;
- proposed canary IDs exactly equal trusted suite IDs;
- counters satisfy `0 <= successes <= valid_runs <= repetitions`;
- every critical canary carries the same complete-stability shape required today;
- proposed QuaLock version equals the trusted runtime version;
- `created_at` is parseable provenance;
- proposed `agent.binary_sha256` is an exact lowercase 64-hex SHA256 expected candidate fingerprint.

Historical proposed canary counters remain provenance/request validation only. They never substitute for the live qualification result.

## 5. Resolver selection and exact candidate identity

Resolver construction moves to the qualification boundary after pure trust validation and credential gating. The PR subsystem owns a dedicated allow-listed factory:

```text
codex  -> CodexResolver(<qualock cache>)
claude -> ClaudeResolver(<qualock cache>)
```

There is no Antigravity branch and no fallback. Do not reuse a resolver factory that can construct `AntigravityResolver`.

An injected `resolver=` seam remains available for unit tests and controlled callers, but it does not bypass identity verification.

After resolving the exact requested version, QuaLock requires both:

```text
resolved_binary.name == CandidateRequest.agent_name
resolved_binary.sha256 == CandidateRequest.binary_sha256
```

A wrong-agent resolver result is rejected even when its hash happens to match. A SHA mismatch is also rejected. Either becomes `INVALID_PROPOSED_LOCK`, not a live qualification verdict.

Resolver/runtime operational failures after a valid request become sanitized `QUALIFICATION_FAILED` INCOMPLETE reports under the existing producer boundary.

Claude resolution retains the Batch #31 minimum-version and native-binary contract. No authenticated Claude execution is required to test this routing change.

## 6. Qualification execution

`qualify_prepared_pr()` re-runs pure proposed-lock validation as defense in depth before any resolver or check call. This keeps direct callers fail-closed even if they did not come through the generated producer workflow.

After pure validation:

1. if the model credential is unavailable, return INCOMPLETE with `CREDENTIAL_UNAVAILABLE`, no resolver/check;
2. construct or accept the allow-listed resolver;
3. resolve the exact candidate version;
4. verify resolved agent name and SHA against `CandidateRequest`;
5. invoke exactly once:

```python
execute_check(
    trusted_base_root,
    f"{candidate.agent_name}@{candidate.version}",
    resolver=resolver,
)
```

Codex therefore remains `codex@<version>` byte-for-byte at the candidate-spec boundary. Claude uses `claude@<version>` and reaches the existing Claude backend/credential-selection contract in `qualock.commands`.

PASS/WARN/BLOCK/INCOMPLETE semantics are not reinterpreted. `report_from_qualification()` remains a sanitized projection of the existing `QualificationResult` and copies `context.agent` into the v2 report.

## 7. GitHub credential isolation

The producer workflow keeps Codex and Claude credentials in separate agent-gated steps. No step receives credentials for an agent other than the trusted context agent.

### Codex

The existing repository secret remains:

`QUALOCK_CODEX_AUTH_B64`

Only when `ready=true && agent=codex`, the workflow decodes it to `$HOME/.codex/auth.json`, sets a boolean availability output, runs Codex qualification, and removes the file in a Codex-scoped cleanup step. The decoded bytes are never echoed or uploaded.

### Claude

Repository secrets are:

- `QUALOCK_ANTHROPIC_AUTH_TOKEN`
- `QUALOCK_ANTHROPIC_API_KEY`
- `QUALOCK_CLAUDE_CODE_OAUTH_TOKEN`

Only when `ready=true && agent=claude`, a single Claude qualification step receives these as the runtime variables `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, and `CLAUDE_CODE_OAUTH_TOKEN`.

The shell computes only a boolean `credential_available`. Before invoking QuaLock it applies the already-certified direct-auth precedence `ANTHROPIC_AUTH_TOKEN` → `ANTHROPIC_API_KEY` → `CLAUDE_CODE_OAUTH_TOKEN` and unsets lower-priority credential variables so the QuaLock child process sees at most one selected non-empty automation credential.

No Claude credential value is written to `GITHUB_OUTPUT`, argv, artifact, file, or sticky comment. Interactive `~/.claude/.credentials.json` is never read or copied.

### Reporter isolation

`REPORTER_WORKFLOW` receives no Codex or Claude model secret. Its permissions and trust bindings remain unchanged: actions read, contents read, statuses write, pull-requests write.

The reporter must not infer agent identity from workflow environment or PR text. It accepts only the validated `context.agent`/`report.agent` binding.

## 8. Producer workflow control flow

The producer continues to use `pull_request_target`, trusted-base checkout, pinned action SHAs, and `persist-credentials: false`.

The generated workflow must have these mutually exclusive expensive paths:

```text
ready && agent == codex
    -> materialize Codex credential -> qualify Codex -> cleanup

ready && agent == claude
    -> qualify Claude with automation env -> no credential file cleanup

otherwise
    -> no model credential step and no live qualification
```

`pr-context.json` is still uploaded before live qualification. `pr-report.json` is uploaded with `if: always()` so terminal prepare reports and runtime failures remain reportable.

No shell command interpolates PR-controlled path/name/body data. The small planning step reads fixed JSON fields and fixed file paths only.

No producer step checks out the PR head, executes PR scripts, installs from the PR head, or sources PR-controlled environment files.

## 9. Safe `qualock github setup` migration

The producer template changes in Batch #39. Existing users with the exact QuaLock-generated Codex-only producer must be able to upgrade without hand-editing YAML, while customized workflows must remain protected from overwrite.

The SHA256 of the current pre-Batch-#39 PRODUCER_WORKFLOW text after the same universal-newline normalization used by Path.read_text() is:

`29648454f323b8816f43ccdc4069c00d14c5c720e10fd9a58f4475a5b1c1ce69`

Setup classifies each workflow before writing anything:

- current new template: identical;
- missing: create new template;
- producer matching exactly the legacy SHA above: eligible for atomic upgrade;
- any other existing producer content: conflict;
- reporter matching current reporter template: identical;
- any modified reporter content: conflict.

All conflict checks occur before writes. A conflict in either file prevents migration of the other.

Legacy detection allows only standard text-mode universal-newline normalization. It performs no whitespace, YAML, or semantic normalization. Only the exact known generated producer is eligible, preventing QuaLock from overwriting a user customization that happens to be structurally similar.

The upgraded producer is written atomically using the existing temporary-file/replace pattern. Setup returns GitHubSetupStatus.UPGRADED so tests can distinguish migration from first creation and idempotent no-op.

A second `qualock github setup` after migration must be `ALREADY_CONFIGURED`.

## 10. Setup UX and documentation

`qualock github setup` continues to print the producer/reporter paths and branch-protection guidance. Its credential instructions become agent-aware without asking the user to expose secret contents to QuaLock.

Codex instructions retain `QUALOCK_CODEX_AUTH_B64` and the existing local base64 recipe for `~/.codex/auth.json`.

Claude instructions tell the user to configure exactly one supported repository secret where practical:

- `QUALOCK_ANTHROPIC_AUTH_TOKEN`, or
- `QUALOCK_ANTHROPIC_API_KEY`, or
- `QUALOCK_CLAUDE_CODE_OAUTH_TOKEN` for subscription automation obtained through `claude setup-token`.

The CLI must not read, print, hash, validate, or inspect those repository secret values. It only explains the names expected by the generated workflow.

README GitHub PR documentation changes from Codex-only to Codex + Claude Code, explicitly states that the trusted base agent selects the credential path, and keeps Antigravity unsupported.

The README must continue warning that `pull_request_target` runs only trusted base code and never checks out the untrusted PR head.

## 11. Reporter and comment semantics

GitHub commit-status mapping remains unchanged:

| QuaLock report | GitHub status |
| --- | --- |
| not applicable | success |
| PASS | success |
| WARN | failure |
| BLOCK | failure |
| INCOMPLETE | error |
| missing/invalid report for upgrade | error |

Statuses remain bound to the validated PR head SHA. Old/superseded runs may affect only their own validated old head and never update a sticky comment after the current PR head changes.

For supported upgrade reports, the sticky comment includes a bounded agent label derived only from `PrAgent`: `Codex` or `Claude Code`. Candidate/baseline versions and qualification ID remain sanitized. No arbitrary proposed-lock or PR text is copied into the comment.

The reporter comment marker stays v1 and bot-owned marker selection behavior remains unchanged.

`UNSUPPORTED_AGENT` may appear only as a bounded reason code. No runtime/auth path or secret name/value is emitted as a failure reason.

## 12. Error contract

Producer failures remain fail-closed and sanitized rather than propagating raw resolver/runtime text into GitHub artifacts.

The producer maps conditions as follows:

| Condition | PR report reason |
| --- | --- |
| trusted suite/config baseline stale or config/baseline agent mismatch | `TRUSTED_BASELINE_STALE` |
| trusted agent not supported by PR qualification | `UNSUPPORTED_AGENT` |
| proposed lock malformed, cross-agent, wrong identity/counters/version/hash | `INVALID_PROPOSED_LOCK` |
| supported agent credential unavailable | `CREDENTIAL_UNAVAILABLE` |
| resolver/check/backend operational failure | `QUALIFICATION_FAILED` |

A resolver identity/SHA mismatch is request validation failure (`INVALID_PROPOSED_LOCK`), because the proposed lock claims a candidate fingerprint that cannot be reproduced from the official package.

CLI hidden command exit behavior remains operational: invalid local artifact/argument shape exits nonzero, while ordinary PR qualification outcomes are represented by the report artifact and GitHub status rather than special shell exit codes.

Reporter binding failures remain publication errors and must never be converted into a success status.

## 13. Security invariants

1. The PR head repository is never checked out or executed.
2. Trusted base config/baseline determines agent identity before any model secret can be exposed.
3. Proposed lock content cannot select the credential family.
4. Pure trust validation performs no resolver/network/runtime work.
5. Invalid proposed locks are terminal before credential staging in the generated workflow.
6. PR resolver construction is allow-listed to Codex and Claude only.

7. Antigravity cannot reach credential staging, resolver construction, `agy`, app data, or qualification.
8. Resolved candidate agent name and SHA must match the trusted request before `execute_check()`.
9. Codex and Claude secret-bearing steps are mutually exclusive.
10. Claude automation uses only documented direct-auth environment variables; interactive Claude credential files are never read/copied.
11. The QuaLock Claude child process sees at most one selected non-empty credential variable after workflow precedence handling.
12. Reporter workflow has zero model-secret references.
13. Model secret values never enter artifacts, `GITHUB_OUTPUT`, command arguments, comments, reason codes, or report fields.
14. Existing GitHub API bounds, event/repository/run/SHA bindings, action SHA pins, and `persist-credentials: false` remain unchanged.
15. Existing sanitized report exclusions remain authoritative: no prompts, task bodies, raw stdout/stderr, `events_jsonl`, auth material, GitHub token, or source content.
16. Workflow migration overwrites only the exact known legacy producer; customized files fail closed.

## 14. TDD strategy

Implementation follows RED → GREEN per task and uses only injected/fake resolvers/backends for new Claude PR routing tests.

### Trusted-agent and prepare tests

- existing Codex upgrade derives `agent="codex"` and remains ready;
- Claude trusted config/baseline derives `agent="claude"`;
- config/baseline mismatch returns terminal `TRUSTED_BASELINE_STALE` before proposed-head read, resolver, credential readiness, or check;
- stale suite/config does the same;
- Antigravity returns `UNSUPPORTED_AGENT` before proposed-head read/resolver/check;
- ordinary and invalid-scope PRs do not load/stage model-agent qualification state;
- proposed-lock GitHub read failure preserves trusted agent when one was established and remains terminal INCOMPLETE.

### Pure proposed-lock validation tests

- valid Codex and Claude requests return `CandidateRequest(agent_name, version, binary_sha256)`;
- cross-agent proposed lock is rejected before resolver/network work;
- `latest`, prerelease, build metadata, equal version, and downgrade remain rejected;
- suite/config/model/canary/QuaLock/timestamp/counter invariants remain covered;
- validation API has no resolver parameter/call;
- prepare-stage invalid request produces terminal report and `ready=false` before any secret-bearing workflow path.

### Resolver and qualification tests

- PR resolver factory maps Codex to `CodexResolver` and Claude to `ClaudeResolver` using the QuaLock cache root;
- no Antigravity resolver import/construction is reachable from the PR factory path;
- injected wrong-agent `AgentBinary` is rejected;
- candidate SHA mismatch is rejected;
- Codex check call remains exactly `codex@<version>` with the same resolver object;
- Claude check call is exactly `claude@<version>` with the Claude resolver;
- missing credential returns INCOMPLETE without resolver/check;
- PASS/WARN/BLOCK/INCOMPLETE projection remains unchanged;
- resolver/check exceptions become sanitized `QUALIFICATION_FAILED`.

### Artifact/reporter tests

- context/report serialize as schema v2 with correct agent/null behavior;
- v1 artifacts are rejected by v2 readers rather than migrated;
- report constructors copy trusted context agent;
- reporter rejects context/report agent mismatch;
- existing repository/run/PR/base/head/QuaLock bindings remain covered;
- sanitized-field and bounded-size tests remain green;
- sticky marker remains exactly `<!-- qualock-pr-report:v1 -->`;
- Claude comment uses bounded `Claude Code` label; Codex rendering has no semantic regression.

### Workflow-template tests

Parse generated YAML and assert structurally:

- producer trigger remains `pull_request_target` with approved activity types;
- trusted base checkout ref and pinned action SHAs remain unchanged;
- producer still has no status/comment write permission;
- reporter still has no model-secret reference;
- Codex secret appears only in the Codex credential path;
- all three Claude repository secrets appear only in the Claude qualification path;
- Claude runtime variable precedence/unset behavior is explicit;
- Codex and Claude qualification conditions are mutually exclusive and require `ready=true`;
- unsupported/null agent enters neither secret-bearing path;
- terminal prepare report skips live qualification;
- reporter template contains none of the four repository model-secret names;
- no PR-head checkout/execution or PR-controlled shell interpolation is introduced.

### Setup migration tests

- exact legacy producer SHA is upgraded atomically to the new producer;
- exact current producer is idempotent;
- modified legacy producer remains conflict and byte-identical;
- reporter conflict prevents producer migration;
- migration followed by a second setup is `ALREADY_CONFIGURED`;
- first-time setup still creates both missing workflows.

### CLI/documentation tests

- setup output retains Codex secret/recipe guidance;
- setup output lists the three Claude repository secret names and `claude setup-token` guidance;
- setup command never inspects credential contents;
- hidden prepare/qualify/report CLI artifact error boundaries remain nonzero and sanitized;
- README describes Codex + Claude PR qualification and Antigravity exclusion.

## 15. Expected implementation scope

Expected production/docs files:

- `src/qualock/github_pr/models.py`
- `src/qualock/github_pr/commands.py`
- `src/qualock/github_pr/report.py`
- `src/qualock/github_pr/publisher.py`
- `src/qualock/github_pr/setup.py`
- `src/qualock/github_pr/templates.py`
- `src/qualock/cli.py`
- `README.md`

Expected tests are the corresponding existing GitHub PR model/command/report/publisher/setup/template/CLI test modules. source.py remains unchanged: schema v2 uses agent=None as the constructor default, so existing PR-context source construction needs no edit. It must not acquire credential/resolver logic.

No production change belongs in release monitor, scheduler, version bisect, Antigravity resolver/adapter/host runner, qualification policy/executor, Docker runner/backend, dependency metadata, or release tooling.

`qualock.commands` already owns working Codex/Claude local backend routing and should not be refactored merely for this batch. The PR subsystem delegates to `execute_check()` rather than forking that logic.

## 16. Final verification gates

Before local-ready declaration:

1. focused GitHub PR model/source/commands/report/publisher/setup/templates/CLI tests;
2. full pytest suite;
3. Ruff on every touched Python file;
4. strict mypy on touched source files, allowing only the repository's known pre-existing PyYAML-stub debt when exposed through dependency traversal;
5. compileall on touched source;
6. `git diff --check`;
7. static scope assertion for forbidden monitor/scheduler/bisect/Antigravity/runtime/dependency paths;
8. static secret assertion proving reporter has no model-secret names and producer agent secrets occur only in the intended mutually exclusive steps;
9. static assertion that the PR resolver path cannot import/construct `AntigravityResolver`;
10. independent whole-branch review against this spec and the implementation plan.

No authenticated Claude or Antigravity run is part of these gates. No dependency/stub installation is permitted.

## 17. Acceptance criteria

Batch #39 is complete only when:

1. existing Codex baseline-only PR qualification retains equivalent trust, candidate-spec, verdict, reporter, and credential behavior;
2. a trusted Claude project can qualify a Claude baseline-only upgrade PR using the existing Claude resolver/backend and direct automation credential contract;
3. trusted base state, not proposed PR state, selects the agent and credential family;
4. invalid/cross-agent proposed locks are rejected before credential staging in the generated workflow;
5. exact candidate binary agent name and SHA are reproduced before live qualification;
6. Antigravity/unknown trusted agents fail closed before model credential, resolver, app-data, or agent execution paths;
7. context/report v2 bind agent identity and reporter rejects mismatches;
8. reporter remains credential-free and all existing SHA/run/repository publication bindings remain intact;
9. exact legacy generated producer workflows migrate safely while customized workflows are never overwritten;
10. no unrelated subsystem or dependency contract changes.

## Deferred follow-ups

A future batch may consider a broader credential-provider abstraction or Antigravity PR qualification only after those workflows have separately specified trustworthy non-interactive credential and release/candidate identity contracts. Neither is implied by Batch #39.
