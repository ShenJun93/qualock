# Batch #44 — Gemini Orchestration Parity Design

**Date:** 2026-09-10
**Base:** `45a2250135317601acedea4d15bcb4632586659d` (`origin/main` after PR #41)
**Branch:** `feat/gemini-orchestration-parity`
**Status:** Delivered

## Goal

Extend the Gemini CLI support delivered in Batch #43 from local qualification into the four orchestration surfaces that were deliberately deferred: release monitoring, native per-user scheduled monitoring, forward version bisect, and GitHub pull-request qualification.

Gemini must reuse the same orchestration engines, persisted state, qualification backend, baseline identity, report semantics, and safety boundaries already used by Codex and Claude Code. Batch #44 must not add a Gemini-specific monitor, scheduler, bisect engine, PR reporter, or verdict policy.

## User-visible outcome

For a project whose trusted baseline uses `agent.name: gemini`:

- `qualock monitor` discovers the latest stable `@google/gemini-cli` npm release and qualifies it through the normal `execute_check` path;
- `qualock schedule enable|status|disable` can register the existing native per-user monitor runner for that Gemini project;
- `qualock bisect gemini@X.Y.Z` scans published stable Gemini releases forward from the trusted baseline using the existing non-monotonic forward-scan semantics;
- `qualock github setup` generates a producer workflow that can qualify a Gemini-only baseline-lock upgrade PR using a repository secret named `QUALOCK_GEMINI_API_KEY`, exposed to QuaLock only as the runtime environment variable `GEMINI_API_KEY` in the Gemini qualification step;
- the reporter continues to publish the existing `qualock/pr` status and sticky report format, with `Gemini CLI` as the agent display name.

Codex and Claude behavior remains unchanged. Antigravity remains unsupported by release discovery, scheduled release monitoring, version bisect, and GitHub PR qualification.

## Non-goals

- No new qualification engine, verdict policy, canary scheduling policy, or grader behavior.
- No authenticated Gemini provider acceptance run as a normal implementation or review gate.
- No browser-login, ADC, service-account JSON, `GOOGLE_API_KEY`, cached Google login, or user `~/.gemini` support for automation.
- No persistence of `GEMINI_API_KEY` or any other credential in scheduler registration/state, monitor state, bisect summaries, PR context/report artifacts, generated files, logs, or Git diffs.
- No daemon, cron fallback, hosted scheduler, hosted runner, secret manager, dashboard, or commercial feature.
- No baseline/result/history/pricing schema-version bump.
- No changes to qualification policy, protected-source materialization, pricing semantics, history semantics, or Gemini stream-json parsing.
- No opportunistic fixes for unrelated Batch #43 final-review minors. The explicit-prerelease stable-release gap is in scope because release discovery and bisect depend on it; unrelated parser/pricing/documentation minors are not implementation scope.

## Design choice

Use a small shared orchestration capability table plus adapter-specific release/resolver/auth hooks.

A direct four-module collection of `if agent == "gemini"` branches would be smaller initially but would duplicate the same support set and make future adapter parity drift likely. A fully generic registry containing resolver factories, credentials, workflow rendering, display names, and policy would be broader than this batch needs.

The chosen middle path centralizes only support capability facts while leaving actual adapter mechanics in the modules that already own them.

## Shared orchestration capabilities

Add `src/qualock/agents/orchestration.py` with one immutable capability record and a pure lookup function. The capability dimensions required in this batch are:

- release discovery / monitor eligibility;
- forward version bisect eligibility;
- GitHub PR qualification eligibility.

Codex, Claude Code, and Gemini CLI have all three capabilities. Antigravity has none. Unknown agent names have none and remain fail-closed.

Scheduler is intentionally not a fourth independent capability: the scheduler only registers the existing release-monitor runner, so scheduler eligibility is inherited from monitor/release-discovery preflight.

The helper contains no resolver instances, credentials, filesystem paths, network calls, or command-layer exceptions. Each owning command module retains its current domain-specific error messages.

## Stable Gemini release contract

### Stable-only exact versions

Batch #43 already requires npm `latest` to be exact stable `X.Y.Z`, but explicit `GeminiResolver.resolve()` currently accepts broader semver including prerelease/build suffixes. Batch #44 closes that gap because orchestration must never scan or schedule preview/nightly releases.

For Gemini V1 orchestration:

- explicit versions accepted by `GeminiResolver.resolve()` are exact stable `X.Y.Z` only;
- `latest` continues to resolve through npm and must produce exact stable `X.Y.Z`;
- preview, nightly, prerelease, build-suffixed, malformed, or non-string registry entries are ignored by stable catalog discovery and rejected when explicitly resolved;
- the minimum certified runtime remains Gemini CLI `0.58.0`.

This is a contract correction, not a new channel feature.

### Stable catalog

Add `GeminiResolver.stable_versions()` matching the existing Codex/Claude catalog interface:

1. run `npm view @google/gemini-cli versions --json` using the existing Gemini npm-probe environment that scrubs Gemini/Google credential variables;
2. fail with `GeminiResolveError` on timeout/non-zero exit/malformed JSON/non-list payload/non-string list members;
3. retain only exact stable `X.Y.Z` versions;
4. deduplicate and return numeric semantic-version order.

`src/qualock/agents/releases.py` gains Gemini wrappers for both `latest_version()` and `stable_versions()`, translating `GeminiResolveError` to the existing `ReleaseDiscoveryError`. Antigravity/unknown-agent errors remain unchanged.

No registry request is made during a qualification attempt itself beyond the existing release-discovery step; each chosen candidate is still resolved and runtime-validated by the normal Gemini resolver.

## Release monitor parity

`MonitorAgent` and `MonitorState.agent` expand from `codex|claude` to `codex|claude|gemini`; `MonitorState.schema_version` stays `1` because the JSON shape is unchanged and existing Codex/Claude states remain valid.

`monitor_preflight()` uses the shared release-discovery capability check instead of a hard-coded Codex/Claude set. It still:

- loads trusted config + baseline;
- validates suite/config freshness;
- requires config agent == baseline agent;
- rejects Antigravity with its existing actionable release-discovery message;
- rejects unknown/unsupported agents fail-closed.

`execute_monitor()` remains agent-neutral. For Gemini it obtains the latest stable version from the default Gemini release source and calls the same `check_executor(root, "gemini@X.Y.Z")`. `monitor_command()` also broadens its final supported-agent guard to admit Gemini and continues to render through the existing generic `agent_display_name()` helper. State matching continues to use the full baseline-lock SHA, so Gemini support-tree identity changes already alter the monitor baseline identity without a new state field.

PASS/WARN/BLOCK terminal states persist exactly as today; INCOMPLETE is not persisted. `--force`, no-downgrade, same-candidate deduplication, corrupt-state recovery, and state-save warning semantics do not change.

## Native scheduler parity

No scheduler backend, native registration format, runner argv, or schema changes are needed.

`enable_schedule()` already calls `monitor_preflight()`. Once monitor preflight admits Gemini, the existing Windows Task Scheduler, `systemd --user`, and launchd backends can register the same fixed runner for a Gemini project.

### Credential rule

Scheduler registration continues to capture only the existing non-secret operational fields, including `PATH`. It must not capture `GEMINI_API_KEY`.

At trigger time, `scheduler.runner` continues to inherit the native user process environment and replace only `PATH` with the registered value. Therefore:

- if the native trigger environment provides `GEMINI_API_KEY`, the child `qualock monitor` can use it through the normal Gemini automation credential path;
- if it does not, the child `qualock monitor` reaches the existing Gemini missing-credential `CommandError` path and exits with the normal command/precondition failure code;
- `qualock schedule enable` does not require, validate, copy, serialize, or snapshot the key.

Tests must prove the registration JSON/native definitions contain no Gemini key and that the runner neither fabricates nor strips a runtime key supplied by its process environment.

## Version bisect parity

`BisectAgent` expands to `codex|claude|gemini`. Bisect summary `schema_version` stays `2`; only the allowed `agent` value broadens and existing summaries remain readable.

`bisect_preflight()` uses the shared version-bisect capability. For Gemini, `execute_bisect()` then reuses the existing flow unchanged:

- upper bound must be exact stable `gemini@X.Y.Z`;
- upper agent must match the trusted baseline agent;
- the version must exist in the frozen Gemini stable catalog;
- upper must be numerically newer than baseline;
- candidates are the sorted stable releases `(baseline, upper]`;
- each candidate runs a normal contemporaneous `execute_check`;
- PASS advances last-known-good;
- BLOCK stops at first confirmed bad;
- WARN or INCOMPLETE stops unresolved;
- summary is saved after every step.

No binary-search assumption is introduced.

## GitHub PR qualification parity

This is the security-sensitive part of Batch #44 because a Gemini baseline has two runtime identity components: the entrypoint digest and the support/runtime fingerprint.

### Agent models and artifacts

`PrAgent` expands to `codex|claude|gemini`. `PullRequestContext.schema_version` and `PullRequestReport.schema_version` remain `2`: their shapes are unchanged and the added enum value is consumed only by the same-version trusted producer/reporter pair.

Publisher display mapping adds `gemini -> Gemini CLI`. Report verdict/reason semantics remain unchanged.

### Trusted Gemini baseline

`_trusted_pr_agent()` continues to load only the trusted base checkout. For a Gemini trusted baseline it additionally requires `trusted.agent.support_sha256` to be present and exactly lowercase 64-hex. Missing/malformed Gemini support identity makes the trusted baseline stale and produces the existing `TRUSTED_BASELINE_STALE` report path.

This check is Gemini-only. Legacy Codex/Claude baseline locks with `support_sha256 = null` remain valid.

### Proposed Gemini lock validation

`CandidateRequest` gains `support_sha256: str | None`.

For a proposed Gemini baseline lock:

- agent must match trusted Gemini agent;
- version must be exact stable and strictly newer;
- suite/config/model/canary/stability/QuaLock-version/timestamp rules remain unchanged;
- `binary_sha256` must remain lowercase 64-hex;
- `support_sha256` is mandatory and must be lowercase 64-hex.

Missing or malformed proposed Gemini support identity yields `INVALID_PROPOSED_LOCK`. Codex/Claude proposed locks retain existing behavior.

### Candidate resolution identity

`_default_pr_resolver()` adds `GeminiResolver(default_agent_cache_root())`.

Before qualification, the trusted producer resolves the exact proposed Gemini version and requires:

- resolved agent name == `gemini`;
- resolved entrypoint SHA == proposed `binary_sha256`;
- `agent_support_fingerprint(resolved)` is non-null and equals proposed `support_sha256`.

Any mismatch is `INVALID_PROPOSED_LOCK`; the qualification is not run.

This prevents a PR from pinning only the small JavaScript entrypoint while presenting a different runtime package tree.

### Workflow credential transport

The fixed producer workflow plan step accepts `gemini` as a ready agent. Add exactly one Gemini qualification step guarded by `steps.plan.outputs.agent == 'gemini'`.

Repository secret name:

`QUALOCK_GEMINI_API_KEY`

The step maps it only to the process environment variable expected by the existing adapter:

`GEMINI_API_KEY: ${{ secrets.QUALOCK_GEMINI_API_KEY }}`

The step uses `set +x`, computes `credential_available` from non-empty `GEMINI_API_KEY`, and invokes the existing hidden `qualock github qualify-pr` command. No credential file is created. The key is not written to `GITHUB_OUTPUT`, artifacts, Docker create argv, generated settings, logs, context/report JSON, or the reporter workflow.

Codex materialization/cleanup and Claude secret precedence remain byte/semantic equivalent except for surrounding template offsets. Reporter workflow remains secret-free.

`qualock github setup` instructions add the Gemini repository-secret name and state that it is for a trusted Gemini baseline only.

### Fork safety

The current `pull_request_target` security model is unchanged:

- checkout trusted base SHA;
- install QuaLock from trusted base;
- inspect untrusted head only as data;
- never check out or execute PR-head code in the producer;
- only a baseline-lock-only PR can trigger qualification;
- reporter still binds workflow run, repository, PR, base/head identity before publishing.

Adding Gemini must not broaden workflow permissions.

## Data compatibility

No migration command is introduced.

- baseline lock schema remains `1`;
- monitor state schema remains `1`;
- bisect summary schema remains `2`;
- PR context/report schemas remain `2`;
- scheduler registration schema/shape remains unchanged.

Existing Codex/Claude persisted data must continue to load unchanged. Antigravity persisted behavior remains unsupported for these orchestration surfaces.

## Error handling

- Gemini npm discovery failures become `ReleaseDiscoveryError` at the release-source boundary.
- Explicit non-stable Gemini versions fail in `GeminiResolver` before install/qualification.
- Missing scheduled Gemini credential is never persisted or guessed; runtime `qualock monitor` fails through the existing Gemini missing-credential `CommandError` path before Docker/provider execution.
- Missing PR Gemini secret yields `CREDENTIAL_UNAVAILABLE`, not a provider call.
- Missing/malformed trusted Gemini support identity yields `TRUSTED_BASELINE_STALE`.
- Missing/malformed/mismatched proposed or resolved Gemini runtime support identity yields `INVALID_PROPOSED_LOCK`.
- Runtime/provider/resolver failures inside PR qualification remain bounded by the existing `QUALIFICATION_FAILED` producer boundary.

No new error/result enum is required.

## Required TDD coverage

1. Shared capability matrix marks Codex/Claude/Gemini supported and Antigravity/unknown unsupported for the correct dimensions.
2. Gemini explicit preview/nightly/prerelease/build versions fail before package install or runtime probe.
3. Gemini `stable_versions()` handles stable filtering, numeric ordering, duplicates, malformed JSON, malformed payloads, npm errors/timeouts, and credential scrubbing.
4. Generic latest/stable release factories route Gemini and still reject Antigravity/unknown agents.
5. Gemini monitor preflight accepts a fresh Gemini baseline while Antigravity/unknown remain rejected.
6. Monitor state schema-v1 round-trips Gemini and still loads existing Codex/Claude states.
7. Gemini monitor latest/no-new/already-qualified/no-downgrade/force/CHECKED behavior reuses existing semantics; INCOMPLETE is not persisted.
8. Scheduler enable accepts a Gemini project without requiring a key at registration time.
9. Scheduler registration/native definitions contain no `GEMINI_API_KEY` or `QUALOCK_GEMINI_API_KEY`.
10. Scheduler runner preserves a runtime `GEMINI_API_KEY` supplied by the native process environment while restoring registered `PATH`; it does not fabricate a missing key.
11. Windows/systemd/launchd scheduler regression suites remain green without Gemini-specific backend branches.
12. Gemini bisect preflight and CLI accept exact stable Gemini upper bounds and reject mismatched/unsupported agents.
13. Gemini bisect uses a frozen stable catalog, scans in numeric order, persists `agent: gemini`, and preserves PASS/BLOCK/WARN/INCOMPLETE stop semantics.
14. PR context/report schema-v2 models round-trip `agent: gemini`; publisher renders `Gemini CLI`.
15. Trusted Gemini PR baseline missing/malformed `support_sha256` is stale; legacy Codex/Claude null support identity remains valid.
16. Proposed Gemini lock missing/malformed `support_sha256` is invalid; valid support identity reaches candidate resolution.
17. Default PR resolver routes Gemini to `GeminiResolver`.
18. Resolved Gemini entrypoint digest mismatch fails before qualification.
19. Resolved Gemini support fingerprint missing/mismatch fails before qualification; exact binary + support match reaches the existing check executor.
20. Missing Gemini PR secret yields `CREDENTIAL_UNAVAILABLE` and no qualification call.
21. Producer workflow plan recognizes Gemini only for baseline-lock upgrade classification.
22. Gemini producer step receives only `QUALOCK_GEMINI_API_KEY -> GEMINI_API_KEY`, does not materialize a file, does not echo/write the secret, and does not expose it to Codex/Claude/reporter steps.
23. Reporter workflow remains free of all provider credentials and workflow permissions remain unchanged.
24. `qualock github setup` output documents Codex, Claude, and Gemini credential choices truthfully.
25. Ordinary/non-baseline PRs remain no-cost/not-applicable; mixed-scope baseline PRs remain invalid.
26. Full Codex/Claude release-monitor/scheduler/bisect/PR regressions remain green.
27. Antigravity remains explicitly unsupported across all four orchestration surfaces.
28. Full repository tests, compileall, diff-check, changed-file Ruff, strict mypy with only established PyYAML baseline debt, and Windows CI remain green.

## Review and implementation sequencing

Implement as separately reviewable TDD tasks, not one broad writer:

1. shared capability matrix + Gemini stable release discovery/stable-only correction;
2. release monitor + state parity;
3. scheduler eligibility/credential non-persistence parity;
4. version bisect parity;
5. GitHub PR model/identity/resolver parity;
6. GitHub producer template/setup/CLI/publisher parity;
7. whole-branch regression, security review, CI, then delivery docs.

Each production task requires observed RED before implementation and an independent scoped review. Critical/Important findings enter a fresh fixer/re-review loop before the next task.

## Verification gates

Before push:

- focused tests for all seven task groups;
- full pytest;
- `compileall`;
- strict mypy, allowing only the established PyYAML `import-untyped` baseline debt;
- Ruff on every changed Python file and no new diagnostics on unchanged files;
- `git diff --check`;
- generated GitHub workflow parse/security assertions;
- protected-scope proof for qualification policy, source materialization, pricing/history behavior, dependency metadata, and unrelated runtime adapters;
- fresh whole-implementation review with zero Critical/Important findings.

After explicit push authorization, hosted CI must be green on the exact implementation head, including Windows. Only then may README/ROADMAP/spec delivery wording be committed. The docs-inclusive head must repeat required local gates, hosted CI, and one final whole-branch review before merge authorization is requested.

## Shared-effect boundary

Design/spec/plan/implementation/testing/review may proceed locally after the relevant approvals. Push, PR creation/update, merge, tag, release, package publish, or any authenticated Gemini provider call requires explicit operator authorization at the applicable boundary.

No tag, release, package publish, hosted/commercial action, or authenticated provider acceptance is part of Batch #44 by default.

## Success criteria

Batch #44 is merge-ready when Gemini has parity with Codex/Claude for release monitor, native scheduled monitoring, forward version bisect, and GitHub baseline-upgrade PR qualification without weakening runtime identity or credential isolation.

The strongest new invariant is that every Gemini orchestration path treats the runtime package support fingerprint as part of trusted identity wherever the baseline/candidate is transported across a persistence or PR boundary. Scheduler remains credential-non-persisting, PR workflows remain trusted-base-only, and all existing agent behavior remains unchanged.
