# GitHub Pull-Request Qualification Reports — Design

Date: 2026-09-03
Project: QuaLock
Batch: #29
Branch: `feat/github-pr-qualification-reports`
Base: `f5a83da8a625fdfa27b7cd75ec07c7bced058e1c`
Status: approved design, implementation not started

## 1. Goal

QuaLock V1 should make coding-agent upgrade decisions visible directly on GitHub pull requests without requiring a maintainer to run a local command or interpret raw CI logs.

The target user experience is:

1. a pull request proposes a new Codex baseline by changing `.qualock/baseline.lock`;
2. GitHub Actions automatically qualifies the proposed Codex version against the trusted current baseline;
3. the PR receives a stable commit status context named `qualock/pr`;
4. agent-upgrade PRs receive one sticky, low-tech comment explaining PASS/WARN/BLOCK/INCOMPLETE and linking to the Actions run;
5. branch protection may require `qualock/pr`.

The implementation must reuse the existing qualification engine, policy, evidence semantics, and baseline freshness rules. It must not create a GitHub-specific grading path.
## 2. Non-goals

Batch #29 does not add:

- a GitHub App;
- hosted QuaLock runners;
- automatic PR creation;
- automatic merge or approval;
- automatic baseline mutation after qualification;
- automatic Codex updates;
- qualification of arbitrary code changes;
- prerelease-agent support;
- a second verdict policy;
- raw agent transcripts in GitHub artifacts or comments;
- remote repository settings changes from `qualock github setup`.

Additional coding-agent adapters and smarter canary/cost controls remain separate roadmap items.

## 3. Core product decision: this is an agent-upgrade gate

V1 does not run expensive agent qualification for every ordinary code PR. Every PR gets a `qualock/pr` status so branch protection remains deterministic, but full qualification runs only when the PR is a narrowly scoped Codex-baseline upgrade.

The only V1 upgrade path is `.qualock/baseline.lock`.
## 4. PR classification

The trusted qualification workflow classifies changed files before any model credential is materialized.

### NOT_APPLICABLE

If `.qualock/baseline.lock` is unchanged:

- do not run `execute_check`;
- emit a sanitized PR report with classification `not_applicable`;
- reporter writes `qualock/pr = success` with description `QuaLock: not an agent upgrade`;
- do not create or update a sticky comment.

### UPGRADE

If `.qualock/baseline.lock` is the only changed path:

- read the proposed lock as data from the PR head through the GitHub API;
- validate it against the trusted base checkout;
- run qualification only if validation succeeds.

### INVALID_SCOPE

If `.qualock/baseline.lock` changes together with any other path:

- do not execute PR code;
- do not run agent qualification;
- emit INCOMPLETE with a fixed reason asking the maintainer to split the agent upgrade into its own PR.
## 5. Threat model and trust boundary

The primary threat is a pull request opened by an untrusted contributor that can control PR metadata and the contents of the proposed `baseline.lock`.

The PR must never be allowed to control executable code in a workflow that has model credentials or a write-capable GitHub token.

Therefore the qualification workflow uses `pull_request_target` only under these hard rules:

- the workflow definition comes from the trusted base repository;
- checkout is pinned explicitly to `github.event.pull_request.base.sha`;
- no checkout of PR head, merge ref, fork repository, or PR artifact containing executable code;
- no `git fetch`/`gh pr checkout` of untrusted code;
- no dependency installation from the PR head;
- no PR-supplied shell fragments, paths, titles, bodies, labels, or branch names are interpolated into shell commands;
- PR head data is retrieved only through GitHub API responses and parsed as bounded data;
- all third-party Actions references in generated workflow templates are pinned to full commit SHAs.

The reporter workflow is triggered by `workflow_run`, has no model credential, and treats every producer artifact as untrusted input. It may parse validated JSON only; it must not execute, source, import, chmod, or run files from the artifact.
## 6. Workflow architecture

Two generated workflows enforce privilege separation.

### Workflow A: `QuaLock PR Qualification`

Trigger:

- `pull_request_target` on `opened`, `reopened`, `synchronize`, and `ready_for_review`.

Permissions:

- `contents: read`;
- `pull-requests: read`;
- no `statuses: write`;
- no comment/write permission.

Concurrency:

- one group per PR number;
- `cancel-in-progress: true`.

Responsibilities:

1. create trusted PR context and classification;
2. upload context metadata early;
3. run the existing QuaLock qualification path when classification is `upgrade`;
4. always attempt to emit a sanitized `pr-report.json` artifact.
### Workflow B: `QuaLock PR Reporter`

Trigger:

- `workflow_run` when `QuaLock PR Qualification` completes.

Permissions:

- `actions: read` to download the producer artifact;
- `contents: read` for trusted reporter code;
- `statuses: write` for `qualock/pr`;
- `pull-requests: write` for the sticky issue comment;
- no model credential.

Responsibilities:

1. verify the triggering run is the expected producer workflow and event `pull_request_target`, then checkout only its trusted `github.event.workflow_run.head_sha`; never substitute a PR head/merge SHA;
2. download artifacts only from `github.event.workflow_run.id` into `${{ runner.temp }}`, never into the repository workspace;
3. validate artifact schema, repository identity, PR number, head SHA, base SHA, and producer run ID;
4. fetch the current PR head SHA before updating the sticky comment;
5. publish the commit status on the artifact's head SHA;
6. update the sticky comment only when that SHA is still the PR's current head;
7. suppress stale comments from cancelled or superseded runs.
## 7. Trusted PR context

Workflow A's first stage produces `pr-context.json` from the event payload plus GitHub API reads.

Required fields:

- `schema_version: 1`;
- repository numeric ID and `owner/name`;
- PR number;
- PR author login;
- trusted base SHA;
- untrusted head SHA;
- producer workflow run ID;
- sorted changed-path list;
- classification: `not_applicable`, `upgrade`, or `invalid_scope`.

Changed paths are obtained from the GitHub Pull Requests API with complete pagination. File names are data only and are never evaluated by a shell.

For an upgrade PR, the proposed `.qualock/baseline.lock` is fetched through the GitHub API at the exact PR head object. The workflow does not clone, checkout, or execute the head repository.

`pr-context.json` is uploaded as artifact `qualock-pr-context` before the expensive qualification stage so the reporter has PR identity even when the later agent run fails. A missing context artifact remains fail-closed; the reporter must not guess a PR or SHA from branch text.
## 8. Proposed baseline validation

The trusted base checkout supplies the authoritative `.qualock/config.yaml`, canaries, grader patches, and current `baseline.lock`.

The proposed lock is accepted as an upgrade request only when all of these checks pass:

- it parses as the current `BaselineLock` schema;
- `agent.name == "codex"`;
- proposed agent version is exact stable `X.Y.Z`, not `latest`, prerelease, or build metadata;
- proposed version is numerically newer than the trusted baseline version;
- proposed `suite_sha256` equals a fresh fingerprint of trusted base canaries;
- proposed `config_sha256` equals a fresh fingerprint of trusted base config;
- proposed model pin equals the effective trusted base model configuration;
- proposed canary IDs equal the trusted base canary IDs;
- proposed canary counters are structurally valid;
- every critical proposed canary satisfies the same stability requirement used by `execute_baseline`;
- the proposed candidate binary SHA256 equals the exact binary resolved from the official Codex package version.

`created_at` is provenance and may differ. The proposed lock's historical canary counters are not used to compute the PR verdict; the live qualification result remains authoritative for this PR gate.
## 9. Qualification execution

After preflight, Batch #29 delegates to the existing engine:

```python
result = execute_check(
    trusted_base_root,
    f"codex@{candidate_version}",
    resolver=resolver,
)
```

The PR subsystem must not duplicate `QualificationExecutor`, `qualify_canary`, `qualify_suite`, Docker execution, source materialization, grader handling, or qualification evidence policy.

This preserves the existing semantics:

- insufficient valid attempts -> INCOMPLETE;
- unstable baseline -> WARN and candidate cannot auto-block;
- stable baseline + full candidate success -> PASS;
- critical stable baseline + candidate zero success -> BLOCK;
- other quality regression -> WARN;
- suite precedence remains INCOMPLETE > BLOCK > WARN > PASS.

The existing `execute_check` continues writing normal local qualification evidence under `.qualock/results/check-.../` on the ephemeral Actions runner. GitHub publication uses a separate sanitized report contract and does not change that evidence format.
## 10. Sanitized PR report contract

The producer uploads `pr-report.json` as artifact `qualock-pr-report`; it must not upload `report.json` because the existing report serialization includes `AttemptResult.events_jsonl`.

`pr-report.json` schema version 1 contains only bounded data:

- repository ID and full name;
- PR number;
- base SHA and head SHA;
- producer workflow run ID;
- classification;
- baseline and candidate versions when applicable;
- qualification ID when a check ran;
- QuaLock version;
- top-level verdict: `pass`, `warn`, `block`, `incomplete`, or `not_applicable`;
- per-canary ID, baseline success/valid counts, candidate success/valid counts, and verdict;
- fixed/bounded reason strings;
- whether model credential was unavailable;
- whether qualification completed normally.

It contains no prompt, task body, raw agent stdout/stderr, `events_jsonl`, auth material, GitHub token, arbitrary PR text, or source-code content.

The producer writes this report atomically and the artifact upload step runs with `if: always()`.
## 11. Commit status semantics

The reporter writes exactly one stable status context:

`qualock/pr`

Mapping:

| QuaLock outcome | Commit status | Meaning |
| --- | --- | --- |
| not applicable | success | ordinary PR; no agent-upgrade qualification required |
| PASS | success | candidate may be adopted under current policy |
| WARN | failure | unresolved quality regression; maintainer review required |
| BLOCK | failure | confirmed blocking regression |
| INCOMPLETE | error | QuaLock could not reach a trustworthy conclusion |
| producer/report failure | error | trusted reporting path failed closed |

WARN remains a QuaLock WARN. Mapping it to GitHub `failure` is a PR-adoption policy decision so branch protection does not silently merge an unresolved upgrade.

The status target URL links to the exact producer Actions run. The description is fixed and bounded to GitHub's status-description limit.

Statuses are written to the report's validated PR head SHA, never to `GITHUB_SHA` from `pull_request_target` or `workflow_run`, because those events run on trusted base/default-branch SHAs.
## 12. Sticky PR comment

Only `upgrade` or `invalid_scope` classifications create a comment. `not_applicable` never comments.

The comment contains a hidden marker such as:

`<!-- qualock-pr-report:v1 -->`

The reporter lists PR issue comments, finds the newest matching marker authored by the GitHub Actions bot, and updates it. If none exists, it creates one. It never edits a human-authored comment solely because that comment copied the marker.

The visible comment is intentionally low-tech:

- title: `QuaLock Agent Upgrade`;
- result: PASS / WARN / BLOCK / INCOMPLETE;
- `Codex old -> new` when available;
- one row per protected workflow/canary using display names when trusted config can resolve them;
- a plain-language recommendation derived from existing safety-summary semantics;
- qualification ID;
- link to the exact Actions run.

The comment never includes raw agent output or arbitrary PR-provided text.

Before updating the sticky comment, reporter fetches the live PR and requires `current_head_sha == report.head_sha`. A superseded run may still publish status to its old SHA, but it must not overwrite the current PR comment.
## 13. Model credential handling

The qualification job is the only job allowed to reference the model credential.

V1 repository secret name:

`QUALOCK_CODEX_AUTH_B64`

It contains base64-encoded contents of the user's Codex `auth.json`. Base64 is transport encoding, not encryption; GitHub's secret store provides confidentiality.

The workflow:

1. creates `~/.codex` with mode 0700;
2. decodes the secret directly to `~/.codex/auth.json` without echoing it;
3. sets mode 0600;
4. runs QuaLock;
5. removes the file in an `always()` cleanup step.

The reporter workflow never references this secret.

If the secret is absent or unavailable, the producer emits INCOMPLETE when it can do so safely. Dependabot-triggered `pull_request_target` runs are expected to have restricted token/secrets; V1 reports INCOMPLETE instead of weakening GitHub's security model or requiring a bypass.
## 14. `qualock github setup`

A new `github` Typer subgroup exposes one user-facing V1 command:

```bash
qualock github setup
```

It creates exactly:

- `.github/workflows/qualock-pr.yml`;
- `.github/workflows/qualock-pr-report.yml`.

Behavior is local and idempotent:

- missing generated file -> create it;
- existing byte-identical generated file -> report already configured;
- existing different file -> fail without overwriting it;
- no `git add`, commit, push, GitHub API write, secret creation, or branch-protection mutation.

After local generation it prints two manual setup requirements:

1. create repository secret `QUALOCK_CODEX_AUTH_B64`;
2. require commit-status context `qualock/pr` in the repository's branch protection/ruleset if the maintainer wants this to gate merges.

The setup output includes a command example for encoding local Codex auth, but does not execute `gh secret set` itself.
## 15. Internal command surface

Generated workflows need stable automation entry points without expanding the everyday CLI surface.

Under the `github` subgroup, V1 may use hidden commands:

- `qualock github qualify-pr` — producer-side trusted classification, preflight, qualification, and sanitized report generation;
- `qualock github report-pr` — reporter-side artifact validation, commit status, and sticky comment publication.

These commands are workflow plumbing, not the primary user experience. They must accept explicit event/report paths and environment-provided GitHub token values without printing secrets.

The Python implementation should remain decomposed behind these commands:

- PR source/API reader;
- classification and proposed-lock validation;
- report model/storage/rendering;
- GitHub publisher client;
- workflow template generation.

Each unit must have a narrow Protocol boundary so GitHub network access can be tested with fakes and normal unit tests do not call github.com.

## 16. GitHub API boundary

Use GitHub's REST API with the standard `GITHUB_TOKEN`; no PAT or custom GitHub App is required in V1.
Producer-side reads require only:

- pull request metadata and changed-file pagination;
- proposed baseline-lock blob/content at the exact head object;
- repository metadata already present in the event where possible.

Reporter-side writes require only:

- workflow artifact download/read;
- live PR metadata read;
- commit-status create with context `qualock/pr`;
- PR issue-comment list/create/update.

All API clients must:

- pin a GitHub REST API version header;
- use bounded timeouts;
- reject non-2xx responses with typed operational errors;
- paginate explicitly where the endpoint can paginate;
- cap downloaded proposed-lock/report size before JSON parsing;
- never follow arbitrary URLs supplied by PR content;
- never log authorization headers or response bodies that could contain tokens.

No GitHub API write occurs in Workflow A. No model/network-agent credential exists in Workflow B.
## 17. Failure semantics

All uncertain states fail closed. There is no fallback from workflow success/failure to a QuaLock verdict.

Producer behavior:

- if context can be established but upgrade validation fails -> emit INCOMPLETE report with a fixed reason;
- if model credential is missing -> emit INCOMPLETE report;
- if `execute_check` raises -> emit INCOMPLETE report referencing no fabricated qualification verdict;
- if report storage fails -> producer job fails; context artifact remains the reporter fallback;
- if context itself cannot be established -> job fails and no PR/head identity is guessed.

Reporter behavior:

- valid `not_applicable` context is sufficient for `qualock/pr = success` even if no expensive report exists;
- upgrade/invalid-scope context without a valid report -> `qualock/pr = error` on the validated context head SHA;
- malformed, oversized, wrong-repository, wrong-run, wrong-PR, or mismatched-SHA report -> error, never PASS;
- GitHub publication API failure propagates as reporter failure;
- if reporter cannot establish a trustworthy head SHA, it writes nothing rather than risking status on the wrong commit. A required `qualock/pr` context therefore remains unsatisfied.

Cancelled/superseded producer runs may update status only on their own old validated head SHA. They must never update the sticky comment after the PR head has changed.
## 18. Proposed-lock provenance boundaries

The proposed lock is an upgrade request, not trusted evidence merely because it is well formed.

The validator therefore treats fields differently:

- `agent.version` selects the candidate after exact-stable validation;
- `agent.binary_sha256` must match the resolver's actual candidate binary;
- `suite_sha256`, `config_sha256`, model pin, and canary IDs must match trusted base-derived values;
- `qualock_version` must equal the trusted runtime QuaLock version that generated the PR report;
- `created_at` is accepted only as a parseable provenance timestamp and does not affect the PR verdict;
- canary stability counters are range-checked and critical stability is required, but those counters do not substitute for live qualification.

The reporter comment must not claim that the CI run reproduced the proposed lock's historical counters. It attests only that the candidate version represented by that lock was qualified under the trusted base project configuration and current QuaLock policy.

A future signed/attested baseline-lock format may tighten this provenance boundary, but it is outside Batch #29.
## 19. Test strategy

Implementation follows TDD and must include unit coverage for each security boundary.

### Classification and source tests

- ordinary PR -> not applicable, no candidate resolve/check;
- baseline-only PR -> upgrade;
- baseline plus any other file -> invalid scope, no check;
- changed-file pagination and deterministic sorting;
- missing/deleted/renamed baseline lock -> INCOMPLETE;
- malformed/oversized proposed lock -> INCOMPLETE;
- no shell execution is used to interpret changed paths or PR metadata.

### Proposed-lock validation tests

- exact stable version accepted;
- `latest`, prerelease, build metadata, equal version, and downgrade rejected;
- wrong agent, binary hash, config hash, suite hash, model pin, QuaLock version, canary IDs, or critical stability rejected;
- baseline lock need not be fetched from PR code to run trusted `execute_check`.
### Qualification and report tests

- upgrade execution delegates to existing `execute_check` with the trusted root and exact candidate spec;
- PASS/WARN/BLOCK/INCOMPLETE are preserved without reinterpretation;
- operational exception becomes PR INCOMPLETE, not PASS/WARN/BLOCK;
- not-applicable/invalid-scope paths never invoke the qualification engine;
- sanitized report includes only allowed fields;
- serialized report contains no `events_jsonl`, prompt/task body, raw stdout/stderr, auth, token, or source content;
- report write is atomic and size bounded.

### Reporter tests

- status mapping is exact, including WARN -> GitHub failure while report verdict remains WARN;
- status is written to validated PR head SHA;
- wrong repository/run/PR/base/head metadata is rejected;
- missing upgrade report with valid context -> error status;
- stale old-head run does not update sticky comment;
- matching current-head run creates then updates exactly one bot-owned marker comment;
- human marker comment is never edited;
- GitHub API failures are surfaced, not converted to success.
### Workflow-template tests

Generated YAML is parsed and structurally asserted:

- producer trigger is `pull_request_target` with only approved activity types;
- reporter trigger is `workflow_run` completed for the producer workflow;
- producer has no status/comment write permission;
- reporter has no model-secret reference;
- producer checkout ref is the trusted base SHA;
- no template contains PR-head checkout, merge-ref checkout, fork checkout, `git fetch`, or `gh pr checkout`;
- downloaded reporter artifacts go under `${{ runner.temp }}`, not the workspace;
- every `uses:` reference is a full 40-hex commit SHA;
- producer/report cleanup steps use `always()` where required;
- concurrency is per PR and cancels superseded producer runs.

### Setup tests

- creates both files when absent;
- second run is idempotent;
- differing existing workflow refuses overwrite and leaves both files unchanged;
- no subprocess/API call performs git commit, push, secret creation, or repository settings mutation;
- output clearly names `QUALOCK_CODEX_AUTH_B64` and `qualock/pr`.

The full existing pytest suite, Ruff, strict mypy on touched Python files, `compileall`, CLI help discovery, and `git diff --check` remain required final gates.
## 20. Expected implementation surface

The plan may refine file names, but the design expects a focused subsystem rather than growing `cli.py` with GitHub logic.

Likely additions:

- `src/qualock/github_pr/models.py` — context/report enums and frozen models;
- `src/qualock/github_pr/source.py` — read-only GitHub PR source client;
- `src/qualock/github_pr/commands.py` — classification, preflight, and `execute_check` orchestration;
- `src/qualock/github_pr/report.py` — sanitized report storage/rendering;
- `src/qualock/github_pr/publisher.py` — status/comment API boundary;
- `src/qualock/github_pr/templates.py` — generated workflow text;
- targeted CLI wiring under a `github` subgroup;
- unit tests for each boundary;
- README and ROADMAP updates after implementation.

Protected qualification modules should remain unchanged unless implementation reveals a concrete missing reusable seam. In particular, do not change `qualification/policy.py` to accommodate GitHub.

## 21. Documentation outcome

README should explain the low-tech path:

1. run `qualock github setup`;
2. commit the two workflow files;
3. add the model-auth repository secret;
4. optionally require `qualock/pr` in branch protection;
5. propose future Codex upgrades by a baseline-only PR.
The docs must explicitly warn that `pull_request_target` becomes unsafe if maintainers modify the generated workflow to checkout and execute PR code while secrets are available.

ROADMAP should move `GitHub pull-request qualification reports` from Next to delivered only after implementation, exact-head verification, and independent review complete.

## 22. Alternatives considered

### A. Normal `pull_request` workflow with secrets

Rejected for V1. Fork PRs intentionally do not receive ordinary Actions secrets, and weakening that policy would undermine the threat model.

### B. `pull_request_target` plus checkout of PR head

Rejected. This is the classic privileged "pwn request" shape. The workflow must never execute PR-controlled code while model secrets are present.

### C. One workflow with both model secret and GitHub write token

Rejected. It is simpler but unnecessarily combines two high-value privileges. `workflow_run` gives a cleaner producer/reporter split.

### D. Checks API / custom GitHub App

Deferred. Commit status `qualock/pr` already supports branch protection, while the sticky comment supplies the richer human-facing result. A GitHub App would add installation, permissions, hosting, and lifecycle complexity not needed for the open-source V1.

### E. Run qualification for every PR

Rejected. QuaLock's current product question is agent-version safety, not arbitrary code correctness; running expensive paired agent canaries on ordinary code PRs would create cost without answering a useful V1 question.
## 23. Security references used for this design

Current GitHub documentation reviewed on 2026-09-03:

- `https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target`
- `https://docs.github.com/en/actions/reference/security/secure-use`
- `https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows`
- `https://docs.github.com/en/actions/concepts/security/github_token`
- `https://docs.github.com/en/actions/tutorials/authenticate-with-github_token`
- `https://docs.github.com/en/rest/commits/statuses`
- `https://docs.github.com/en/rest/issues/comments`
- `https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-on-actions`

These sources establish the design constraints that privileged `pull_request_target`/`workflow_run` contexts must not execute untrusted PR content, that `GITHUB_TOKEN` should use least privilege, that workflow artifacts crossing into a privileged reporter must be treated as untrusted data, and that commit statuses can provide a stable branch-protection context.

## 24. Definition of done

Batch #29 is locally complete only when:

- both generated workflows satisfy the security invariants above;
- agent-upgrade PR qualification delegates to existing `execute_check`;
- sanitized publication cannot leak raw attempt transcripts;
- status/comment behavior is fully unit tested, including stale-run races;
- setup is local-only and idempotent;
- full project verification passes on one exact HEAD;
- independent review reports no Critical or Important findings;
- working tree is clean.
