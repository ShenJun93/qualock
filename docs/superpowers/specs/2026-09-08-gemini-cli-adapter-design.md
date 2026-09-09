# Batch #43 — Gemini CLI Mainline Adapter Design

**Date:** 2026-09-08
**Base:** `a98374d5c161ef96272e0a94a5f7584511e7f7f9` (`origin/main` after PR #40)
**Branch:** `feat/gemini-cli-mainline-adapter`
**Status:** Approved design; implementation planning in progress

## Goal

Add Gemini CLI as QuaLock's fourth local qualification agent, alongside Codex, Claude Code, and Antigravity, without weakening the generic qualification, grading, evidence, pricing, history, or verdict contracts already delivered on `main`.

Users can run local `baseline` and `check` flows with `gemini@<version>` after explicitly selecting `agent.name: gemini`, a Gemini model, and provider-default reasoning behavior. `qualock start`, `doctor`, `history`, and `cost` must remain composable with the new agent. Existing Codex, Claude Code, and Antigravity behavior stays unchanged.

The design deliberately ports lessons from the historical `feat/gemini-cli-adapter` prototype, but no prototype commit is cherry-picked wholesale because that branch predates Antigravity, agent-aware release/bisect/PR work, token budgeting, history, and provider-specific pricing.

## Why Gemini CLI

Gemini CLI is the next adapter because its official stable package exposes exact npm versions, headless prompting, model selection, structured `stream-json` output, tool-use events, and terminal usage statistics. As of 2026-09-08, npm `latest` is `0.58.0`; preview and nightly channels remain outside this batch.

Gemini materially differs from the existing adapters: its published CLI is a JavaScript application requiring Node.js >=20. Batch #43 therefore adds one generic immutable Node runtime-overlay concept instead of hiding Node-specific behavior inside Gemini-only Docker branches.
## Non-goals

- No Gemini GitHub PR qualification, version bisect, scheduled release monitor, or release-monitor state migration in Batch #43. Those become Batch #44 parity work after the local adapter is stable.
- No Google browser-login flow, cached OAuth reuse, ADC, Vertex AI, service-account JSON, `GOOGLE_API_KEY`, or personal Google-login free-tier automation.
- No preview/nightly channel semantics; `latest` means the npm stable tag and must resolve to an exact version.
- No undocumented mapping from QuaLock `low|medium|high|xhigh` to Gemini thinking controls.
- No Gemini-native Docker sandbox, Docker-in-Docker, Podman-in-Docker, LXC, host Docker socket, host networking, or privileged containers.
- No retroactive pricing of old qualifications, no pricing lookup at runtime, and no monetary qualification gate.
- No Alpine/musl certification in this batch.
- No refactor of qualification verdict mathematics or protected-path policy.

## Public configuration contract

`AgentConfig.name` becomes `Literal["codex", "claude", "antigravity", "gemini"]`; Codex remains the generated default.

`parse_agent_spec` accepts `gemini@<version>` and `gemini@latest`. Config, baseline lock, and candidate agent names must still match before resolution or execution.

Display names are `Codex`, `Claude Code`, `Antigravity`, and `Gemini CLI`.

`ModelConfig.reasoning_effort` adds `provider-default`. Gemini V1 requires exactly `provider-default`; Codex, Claude Code, and Antigravity reject it before resolution. Gemini rejects `low|medium|high|xhigh` rather than silently ignoring them. Existing baseline/history/pricing structures already persist reasoning effort as strings, so no artifact schema bump is required.

Gemini users must explicitly configure a Gemini-supported model identifier. QuaLock does not translate GPT/Claude/Antigravity model names, auto-select a model family, or substitute another model after provider rejection.
## Validated external contract

Batch #43 certifies stable Gemini CLI `0.58.0` as the minimum validated version. Lower versions fail closed until separately certified. `latest` may resolve to a newer stable release only if that exact package passes all QuaLock runtime-contract probes; latest is not unconditional compatibility.

Required official CLI behavior:

- `-p/--prompt` triggers non-interactive execution;
- `--model` selects the configured model;
- `--output-format stream-json` emits newline-delimited `init`, `message`, `tool_use`, `tool_result`, `error`, and terminal `result` events;
- `--approval-mode=yolo` permits required write/shell tools in headless mode;
- `--extensions` controls extension loading;
- `GEMINI_CLI_HOME` redirects user-level state;
- `GEMINI_API_KEY` is a supported non-interactive auth source;
- published stable package requires Node.js >=20.

The resolver must validate the exact installed package, not only documentation. Required flags, exact version output, npm bin entrypoint, and the Linux shell PATH-interception contract are certified from the cached package before an `AgentBinary` is returned.

## Gemini resolver

Create `src/qualock/agents/gemini_resolver.py` with `GeminiResolver` and `GeminiResolveError`.

Exact stable versions and `latest` are accepted. Fresh `latest` resolution uses `npm view @google/gemini-cli version`; exact requests use the requested stable version. Preview/nightly identifiers are rejected in V1.

Cache layout is under QuaLock's normal agent cache, keyed by exact version. Resolution installs the exact published package with lock metadata and no mutable credentials or user config, then resolves and validates `package.json` `bin.gemini`.

Gemini CLI 0.58.0 publishes `bin.gemini` as `bundle/gemini.js`, but mandatory no-auth runtime proof established that this entrypoint is not standalone. Runtime code also references nested package paths such as `bundle/worker/`, so the executable closure is the published package tree rather than only same-directory chunks. `AgentBinary.path` still points to the validated entrypoint and `AgentBinary.sha256` remains the SHA-256 of that executable file, preserving existing core digest semantics. The resolver additionally fingerprints the complete regular-file package tree, rejects any symlink anywhere in that tree, and exposes the tree as one read-only support mount whose container layout preserves package-relative paths. Cache metadata is validated separately and is not silently treated as executable identity.
Fresh resolution must use an exact dependency/lock installation with lifecycle scripts disabled and unrelated audit/funding side effects disabled. Cache reuse performs no registry access. Malformed registry metadata, missing npm bin entrypoint, executable/cache tampering, symlink escape, unsupported Node/runtime contract, or exact-version mismatch fail closed.

The resolver reads `package.json` `bin.gemini`; it must not assume `dist/index.js`. This carries forward a concrete prototype correction.

## Generic immutable runtime overlay

Extend the generic agent contract with an immutable runtime overlay used during prepared-image construction. An overlay identifies:

- a digest-pinned source container image;
- a source filesystem path inside that image;
- a deterministic destination path in the prepared canary image.

Gemini requests one official Node 22 Bookworm-compatible overlay copied into `/opt/qualock/node-runtime`. The image reference must contain `@sha256:` and is a QuaLock code constant, not user input. Floating tags are rejected.

`DockerQualificationBackend.prepare` forwards overlays generically. `DockerRunner.prepare` adds deterministic named multi-stage `FROM <digest>` stages and `COPY --from=...` operations before canary setup commands. Existing adapters return no overlays, so Codex/Claude/Antigravity prepared-image behavior must remain unchanged when the overlay tuple is empty.

The resulting prepared-image digest is the authority for the exact canary runtime plus Node overlay. Gemini V1 supports only glibc/Bookworm-compatible prepared images that can execute the pinned Node runtime; incompatible images fail preparation instead of installing a different Node version.

## Gemini invocation adapter

Create `src/qualock/agents/gemini.py` implementing the existing `AgentAdapter` protocol.

The adapter invokes the pinned package in headless mode with exact configured model, `--output-format stream-json`, `--approval-mode=yolo`, and no interactive resume/session behavior. Extensions must be disabled explicitly; no extension discovery fallback is allowed.

YOLO is necessary because headless Gemini denies write/shell tools that would otherwise require confirmation. It grants no host authority: the outer QuaLock prepared container remains the filesystem/process boundary, protected-path checks remain authoritative, and every Gemini shell child enters the separate network sandbox defined below.
## Configuration and project-state isolation

Gemini must not consume user or repository Gemini configuration during qualification.

The invocation sets both `HOME` and `GEMINI_CLI_HOME` to a private ephemeral location under `/opt/qualock/gemini-home`. No host `~/.gemini`, trusted-folders file, session history, OAuth state, extension state, or settings are mounted.

`/workspace/.gemini` is hidden behind a QuaLock-owned read-only empty bind mount for the agent phase. This blocks project settings, hooks, skills, extensions, MCP definitions, `.gemini/.env`, and other Gemini-specific project state without deleting or mutating the canary source. Bind mounts are not committed into the frozen image, so grader visibility of the original repository remains unchanged.

A QuaLock-owned system settings file is mounted read-only and selected with `GEMINI_CLI_SYSTEM_SETTINGS_PATH`. It must:

- set `advanced.ignoreLocalEnv=true`, preventing generic project `.env` loading;
- set `context.fileName=[]` and disable extra context-directory loading, preventing root/nested `GEMINI.md` context injection;
- disable telemetry and usage statistics;
- disable auto-update/update notifications and session checkpointing;
- keep MCP server maps, policy paths, hooks, and extension configuration empty;
- restrict the model-visible core tools to the minimal coding surface required by QuaLock canaries;
- block web fetch/search/browser/sub-agent/skill activation and other non-V1 tool surfaces.

Because some Gemini settings objects/arrays merge rather than replace cleanly, isolation must not rely on precedence alone: hostile project `.gemini` is physically masked and user state is redirected before the process starts.

A real no-auth isolation test must prove sentinel values in repository `GEMINI.md`, `.gemini/settings.json`, `.gemini/.env`, and user-home-like state do not appear in effective invocation behavior or evidence.
## Shell network isolation

The Gemini parent process needs outbound access to the model API, so the outer container cannot simply use `--network none`. Shell-tool children must nevertheless be offline.

The adapter therefore prepends a QuaLock-owned `/opt/qualock/bin` to `PATH` and mounts an executable `bash` wrapper there. The resolver certifies that the installed Gemini package still resolves Linux shell execution through a non-absolute `bash` found via `PATH`. If a future package switches to an absolute shell path or another execution mechanism, resolution fails closed until the boundary is reviewed.

The wrapper must unset Gemini/Google credential variables, then execute Bubblewrap with both `--unshare-user` and `--unshare-net`. Both preflight and real execution include `--bind / /` plus Bubblewrap-managed `--dev /dev`. Prototype probing established that `--unshare-net` alone is insufficient under QuaLock's existing outer container security mode and that omitting `--dev /dev` breaks ordinary shell tooling.

There is no direct `/bin/bash` retry or unsandboxed fallback. Namespace setup failure emits fixed sentinel `QUALOCK_GEMINI_SHELL_SANDBOX_FAILURE` and exits `125`.

The real wrapped child emits exactly one QuaLock-owned terminal marker `QUALOCK_GEMINI_EXIT_CODE=<0..255>` after Bubblewrap returns and exits with the same status. Gemini's `tool_result.status` is not trusted as a shell exit code: authenticated prototype evidence showed it can report tool invocation success even when the underlying command fails.

The parser accepts shell exit status only from exactly one well-formed marker in the matching tool result. Missing, duplicate, malformed, or out-of-range markers fail closed. A future Gemini runtime that bypasses the wrapper therefore cannot silently turn failed or unsandboxed shell execution into trusted evidence.

No Batch #43 change may add `--privileged`, `SYS_ADMIN`, host networking, Docker socket access, host device passthrough, or any new outer-container security relaxation. The existing DockerRunner security mode is an input to this design, not expanded scope.
## Automation credential isolation

Gemini V1 supports only `GEMINI_API_KEY` automation authentication.

QuaLock must not read or mount Google-login tokens, browser auth state, ADC files, service-account JSON, `~/.gemini`, project `.env`, or project `.gemini/.env`. Missing or empty `GEMINI_API_KEY` fails before Docker execution with a fixed actionable `CommandError`.

The secret uses the existing generic `stdin_secret_env` transport. The value is streamed only when the already-created container starts and exported into the Gemini parent process. It must never appear in Docker create argv, Docker environment metadata, bind-mounted files, generated settings, package cache, result artifacts, pricing sidecars, test fixtures, or Git diffs.

The shell wrapper unsets `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_PROJECT_ID`, and `GOOGLE_CLOUD_LOCATION` before tool-child execution. Shell network isolation remains the primary exfiltration boundary if upstream child-environment filtering changes.

`doctor` for a Gemini-configured project checks Git, npm, Docker readiness, canaries, and presence of a non-empty `GEMINI_API_KEY`. It does not validate the key by making a provider request.

## Gemini stream-json evidence

Create `src/qualock/evidence/gemini_stream_json.py` with `GeminiEvidenceError(AgentEvidenceError)` and `parse_gemini_stream_json(lines) -> AgentEvidence`.

The parser is strict on malformed JSON, non-object events, missing terminal result, duplicate terminal result, malformed required fields, unsafe workspace paths, and ambiguous shell exit markers. It is tolerant of additive unknown event types/fields and records unknown events without guessing semantics.

Normalization contract:

- `init.session_id` -> `thread_id` when valid;
- `init.model` and terminal per-model stats provide observed model provenance;
- `tool_use` for `run_shell_command` records the exact command;
- matching shell `tool_result` gets exit status only from one valid QuaLock marker;
- `write_file`/`replace` tool use records validated workspace file-change evidence;
- web/search/browser tool events populate `web_searches`;
- MCP-origin tool events populate `mcp_calls`;
- terminal error status/error data populates `errors`;
- terminal aggregate/per-model stats provide token usage without summing intermediate message events.
Token mapping must be fixture-driven. For the validated 0.58.0 contract, top-level `input_tokens`, `output_tokens`, and `cached` are accepted only as non-negative integers. If `stats.models` exposes per-model `tokens.thoughts`, QuaLock aggregates thoughts exactly once into `reasoning_output_tokens`; absent thought detail must never be inferred from prose or `total_tokens`. Tool-token fields are not silently added to billable output unless the pinned public rate card explicitly requires that category.

Ordinary parser implementation uses official 0.58.0 schema/source snapshots plus clearly named synthetic protocol-conformance fixtures and the already-reviewed historical prototype observations. A sanitized real stream-json fixture is required only when the optional authenticated acceptance is explicitly authorized and run; it must lock the observed event names, shell parameters, tool-result shape, model fields, and terminal stats. No authenticated fixture may be fabricated when a key is absent.

## Routing and local command integration

Only generic routing helpers gain Gemini branches. Qualification policy, executor scheduling, grading, and verdict code must not gain agent-specific Gemini branches.

- `parse_agent_spec`, display-name routing, resolver routing, and Docker adapter routing add `gemini`.
- `baseline` pins `AgentPin(name="gemini", version=<exact>, binary_sha256=<validated bin.gemini SHA-256>)`.
- `check` retains config/baseline/candidate equality checks before resolution.
- `doctor` adds Gemini credential/readiness checks without provider network calls.
- `init` continues to generate the existing Codex default config; Gemini is opt-in by editing configuration.
- `start` remains agent-neutral; no Gemini-specific start/watch state machine is added.
- `history` remains provider-neutral and must not import the Gemini adapter or parser.

Batch #43 must not extend GitHub PR qualification, version bisect, scheduled release monitoring, or their persisted agent literals. Those modules continue to reject unsupported Gemini usage with their existing safe unsupported-agent behavior until Batch #44.

## Pricing and reference-cost integration

Gemini maps to provider `google`, but model resolution must branch by **agent**, not merely provider, because Antigravity and Gemini share the provider while exposing different configured/runtime model evidence.

Gemini pricing identity requires runtime observation from stream-json `init.model`/terminal model breakdowns. Across a qualification, all non-empty observed models must agree. Malformed model evidence, no observation, conflicting observations, or a configured exact canonical model that disagrees with the runtime observation produce the existing fixed unavailable reasons rather than guessed pricing.

If the agreed observed model is not represented by a pinned Google rate card, pricing sidecar generation records `unknown_model`/`no_rate_card` as applicable and remains advisory. Qualification output, exit status, PASS/WARN/BLOCK/INCOMPLETE verdict, canonical result artifacts, history loading, and token budgeting are unchanged.
Gemini usage-detail trust is conservative:

- terminal `stats.cached` may mark `cached_input_tokens` as observed only when exactly one structurally valid terminal result proves a non-negative integer;
- cache-write tokens remain `unobserved` unless a future certified stream field proves them directly;
- missing/duplicate/malformed terminal results yield unobserved trust, never known-zero by assumption.

`qualock cost` may therefore price a Gemini run only when the existing pricing pipeline has a pinned rate card plus sufficient usage trust. Otherwise it renders the normal truthful unavailable guidance and exits zero. No catalog/network fetch occurs during `cost`.

## Backward compatibility and protected scopes

Existing Codex, Claude Code, and Antigravity local behavior is a hard regression boundary. In particular:

- empty runtime-overlay tuples must preserve current Docker preparation behavior;
- no host-runner behavior for Antigravity changes;
- no existing resolver cache layout or digest meaning changes;
- no baseline/result/history/pricing schema-version bump;
- no changes to `qualification/policy.py` or verdict mathematics;
- no changes to token/attempt budget semantics;
- no changes to release-monitor, scheduler, version-bisect, or GitHub-PR production behavior;
- no changes to provider-specific rate cards except adding a Gemini mapping only where an already-pinned Google canonical model is truly shared;
- no dependency addition to QuaLock's Python package metadata.

The generic runtime-overlay addition must be isolated behind default-empty data so non-Gemini adapters do not need Gemini/Node knowledge.

## Error handling

- malformed/unsupported versions, npm failures, executable/cache integrity mismatch, missing bin entrypoint, unsupported CLI flags, shell-interception mismatch, or exact-version mismatch raise `GeminiResolveError`;
- incompatible Node overlay/runtime execution fails during prepare, with no distro Node fallback;
- unsupported reasoning effort fails before resolver/network activity;
- missing API key fails before Docker execution;
- malformed stream-json or missing/duplicate terminal result raises `GeminiEvidenceError`;
- non-zero Gemini process exit invalidates the attempt under the existing backend rule even if partial evidence exists;
- shell sandbox setup/marker failure invalidates evidence; no unsandboxed retry is permitted.
## Security invariants

1. No interactive Gemini/Google credential state is read or mounted.
2. `GEMINI_API_KEY` enters through stdin-secret transport only and is absent from persisted metadata/artifacts.
3. User Gemini state is redirected to an ephemeral private home.
4. Project `.gemini` is masked during the agent phase without mutating the frozen source used by the grader.
5. Repository/global `GEMINI.md` context loading is disabled.
6. Generic/project `.env` loading is disabled for Gemini CLI.
7. Extensions, hooks, MCP, skills/sub-agents, web fetch/search, and browser tools are absent from the V1 model tool surface.
8. Gemini's parent process may reach the provider API; every model-invoked shell child must enter a fresh user namespace and network namespace.
9. Shell namespace setup or exit-evidence failure is fail-closed.
10. The isolated shell receives Bubblewrap's minimal `/dev`; no host-device passthrough is added.
11. The Node runtime comes only from a digest-pinned immutable overlay.
12. The exact Gemini package version and bundled executable are validated, and the executable digest is pinned before execution.
13. No Docker socket, privileged mode, host network, new capability, or host namespace is exposed.
14. Existing frozen-state protected-path inspection remains authoritative after execution.
15. Pricing remains advisory and cannot alter qualification verdicts or canonical result artifacts.
16. Existing Codex, Claude Code, and Antigravity security contracts remain unchanged.

## Required TDD coverage

1. Config accepts `gemini` and `provider-default`; incompatible agent/effort combinations fail before resolution.
2. Agent-spec parsing/display routing accepts Gemini while legacy strings remain unchanged.
3. Gemini resolver exact/latest/stable-only behavior, npm failures, malformed registry metadata, and cache reuse.
4. Executable digest determinism plus canonical support-tree digest determinism; cache tamper detection; nested file/symlink mutation rejection; and no-registry cache reuse.
5. Resolver reads `package.json` `bin.gemini` and verifies exact reported version plus every adapter-used flag.
6. Resolver rejects installed packages whose Linux shell path no longer resolves `bash` through `PATH`.
7. Generic runtime overlay rejects floating image refs and emits deterministic Dockerfile stages/copies.
8. Existing adapter preparation remains unchanged when no runtime overlay is requested.
9. Adapter argv pins prompt/model/stream-json/approval mode, disables extensions, and uses private HOME/GEMINI_CLI_HOME.
10. Host/user/project Gemini config sentinels cannot influence effective invocation.
11. System settings disable local env, context memory, telemetry, update/checkpoint behavior, MCP/hooks/policy paths, and non-V1 tools.
12. API-key transport proves the secret is absent from create argv, Docker environment metadata, mounts, settings, artifacts, and rendered output.
13. Bubblewrap wrapper contains `--unshare-user`, `--unshare-net`, `--bind / /`, and `--dev /dev`; credentials are unset before execution and no direct-shell fallback exists.
14. No-auth Docker smoke proves parent network remains reachable while wrapped child direct-IP/DNS access is unreachable, local commands still work, `/dev/null` works, exit markers exactly preserve child status, and a separate `DockerQualificationBackend.run_attempt` version probe exercises production support-tree mounting without a provider request.
15. Parser covers init/model, shell/file tools, web/MCP classification, terminal success/error, usage stats, cache/thought tokens when structurally present, unknown events, malformed JSON, and missing/duplicate results.
16. Shell parser accepts exactly one marker in range `0..255` and fails closed on missing, duplicate, malformed, or spoof-like marker evidence.
17. Routing pins both Gemini bundled-entrypoint digest and Gemini support/runtime fingerprint in baseline, rejects missing/mismatched Gemini support identity as stale, and leaves legacy non-Gemini locks without `support_sha256` valid.
18. `doctor` is offline and actionable for missing Docker/npm/key/canaries.
19. `history` public output remains unchanged and continues to have no reverse dependency on pricing/Gemini modules.
20. Pricing maps `gemini -> google`, distinguishes Gemini model observation from Antigravity mapping, and records fixed unavailable reasons on unknown/conflicting/missing evidence.
21. Gemini pricing trust marks cache-read observed only from one valid terminal result and never invents cache-write trust.
22. `qualock cost` remains read-only/offline, prices only pinned/trusted Gemini history, and otherwise exits zero with normal unavailable guidance.
23. Release monitor, scheduler, version bisect, and GitHub PR tests prove Gemini remains unsupported in those flows for Batch #43.
24. Full Codex/Claude/Antigravity local qualification, history, token-budget, and pricing regression surfaces remain green.

## Mandatory no-auth acceptance

Before whole-branch review, run a Docker-backed contract that requires no Gemini credential and no provider request. It uses the actual prepared-image/adapter wrapper path and proves:

- pinned Node overlay executes in the supported Bookworm-compatible canary image;
- actual cached Gemini package passes version/help/bin/shell-interception probes;
- project/user configuration sentinels are isolated;
- wrapper preflight/local command/`/dev/null` work;
- parent direct network is available while wrapped child direct network is unavailable;
- deliberate child exit `7` emits exactly one `QUALOCK_GEMINI_EXIT_CODE=7` and returns `7`;
- no privilege/capability/host-network/device escape is added.
## Optional authenticated acceptance

Authenticated provider execution is **not** a normal Batch #43 implementation/review gate. Do not run Gemini CLI against the provider merely because `GEMINI_API_KEY` happens to exist in the environment.

Only after explicit operator authorization may an opt-in acceptance run one bounded headless qualification. That optional run should prove parent API access, one file edit, local shell execution, blocked shell network, stream-json model/usage fields, secret non-leakage, and hidden-grader success. Any captured transcript must be sanitized before becoming a fixture.

If no authenticated run is authorized, implementation relies on official 0.58.0 headless schema/source snapshots plus the already-reviewed historical prototype observations for the shell marker correction. It must not fabricate provider-specific usage fields that those sources do not prove.

## Mainline integration sequence

Implementation should preserve these dependency boundaries:

1. generic runtime-overlay data + Docker preparation support;
2. Gemini resolver/cache/runtime contract;
3. Gemini adapter isolation/auth/shell wrapper;
4. Gemini stream-json parser;
5. config/local routing + doctor;
6. pricing provenance/trust integration;
7. no-auth real-package/Docker contract;
8. whole-branch regression/review and post-CI docs.

Each production task requires TDD and an independent scoped review before the next branch writer proceeds. Critical/Important findings return to a fresh correctly scoped fixer and exact-head re-review.

## Documentation delivery rule

README/ROADMAP/spec status must not claim Gemini delivery before implementation-head CI is green. After exact implementation CI passes, docs may describe local Gemini `baseline`/`check` support, API-key-only automation, provider-default reasoning, isolation boundaries, and deferred Batch #44 parity work.

The roadmap line `Additional coding-agent adapters` moves to Delivered only after final docs-inclusive CI and review. No tag, release, package publish, or hosted/commercial action is part of Batch #43.

## Final verification gates

- focused Gemini resolver/adapter/evidence/runtime-overlay/routing/pricing tests;
- full pytest;
- compileall;
- strict mypy with only the established PyYAML baseline debt allowed;
- Ruff on all changed Python plus no-new full-tree Ruff debt versus `a98374d`;
- `git diff --check`;
- protected-scope proof for qualification policy/verdict mathematics and unrelated orchestration modules;
- Linux CI across the repository's supported Python matrix and Windows CI;
- independent whole-implementation review before push and one final docs-inclusive whole-branch review before merge.
## Success criterion

A Gemini `baseline` and `check` run through the same generic Docker qualification backend, executor schedule, hidden grader, evidence normalization, token budgeting, pricing sidecar capture, history loader, and verdict policy used by current mainline agents. The only new generic infrastructure is immutable runtime-overlay support plus a read-only fingerprinted support-tree mount needed to reproduce the exact published JavaScript package layout.

The adapter must demonstrate that a network-dependent parent coding agent can coexist with network-denied shell children without exposing credentials, project Gemini configuration, host state, or a Gemini-specific qualification engine.

### Task 6 runtime-closure and identity correction

Mandatory no-auth execution disproved the earlier single-file runtime assumption and fresh independent review identified three load-bearing gaps. The correction is binding for the remainder of Batch #43:

1. **Support-tree primitive.** `AgentBinary` gains zero or more immutable `AgentSupportTree` records containing host root path, canonical tree SHA-256, and absolute container root. Existing `AgentSupportBinary` remains unchanged for single support executables. Gemini 0.58.0 uses exactly one support tree rooted at the resolved `@google/gemini-cli` package root and mounted read-only at `/opt/qualock/gemini-package`.
2. **Canonical tree fingerprint.** Fingerprinting walks the resolved package root without following symlinks, rejects every symlink (file or directory), and hashes every regular file. The digest input is a deterministic sorted sequence of package-relative POSIX path and that file's SHA-256; absolute host paths, mtimes, modes, uid/gid, and directory enumeration order are not identity inputs. The resolver computes the tree fingerprint before and after version/help/source contract validation; any change fails closed.
3. **Baseline runtime identity.** `AgentPin` gains optional `support_sha256`. A new Gemini baseline writes the combined runtime-support fingerprint derived from its support-tree metadata. On `check`, Gemini requires this field and rejects a missing or mismatched value as a stale baseline. Existing Codex/Claude/Antigravity locks that predate this field remain valid with `support_sha256 = null`; no baseline schema-version bump is required. `binary_sha256` retains its current meaning and remains the executable-entrypoint SHA.
4. **Execution-time integrity.** Immediately before Docker create, the generic backend revalidates every declared support binary/tree against the digest carried by the resolved `AgentBinary`; a missing file/tree, symlink, non-regular support file, or digest mismatch fails before agent execution. This check is generic but does not change existing baseline identity rules for non-Gemini agents.
5. **Layout preservation.** The support tree is mounted as one read-only directory, preserving nested `bundle/worker`, `bundled`, `policies`, `builtin`, and any other published package-relative runtime paths. This avoids hundreds of bind mounts and does not require predicting which nested assets a future headless code path will touch.
6. **Production-path no-auth proof.** Task 6 must exercise real `DockerQualificationBackend.run_attempt`, not a reconstructed Docker argv path. A test-only probe adapter may delegate Gemini runtime overlays and isolation mounts while replacing the agent argv with exact cached Gemini `--version`; its parser returns neutral `AgentEvidence`. This proves production source materialization, support-tree plumbing, Docker create/start/commit, frozen-state inspection, grader invocation, and cleanup without `--prompt`, credentials, provider requests, or authenticated Gemini execution.
7. **Scope boundary.** This correction does not add release-monitor, scheduler, version-bisect, or GitHub-PR Gemini parity; Batch #44 still owns those surfaces. It does not authorize an authenticated provider run.

Existing Codex, Claude Code, and Antigravity local behavior remains unchanged. Batch #43 ends at merge-ready local Gemini support; orchestration parity is Batch #44.

## External contracts checked for this design

Official sources checked on 2026-09-08:

- Gemini CLI reference: `https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/cli-reference.md`
- Headless/stream-json schema and exit codes: `https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md`
- Configuration precedence, `GEMINI_CLI_HOME`, `.env`, `ignoreLocalEnv`: `https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md`
- Hierarchical `GEMINI.md` context: `https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/gemini-md.md`
- Enterprise tool allowlisting/security notes: `https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/enterprise.md`
- Shell tool contract: `https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/shell.md`
- Non-interactive auth validation: `https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/validateNonInterActiveAuth.ts`
- Gemini API-key validation: `https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/config/auth.ts`
- Stream-json upstream snapshots: `https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/__snapshots__/nonInteractiveCli.test.ts.snap`
- Token categories: `https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/chatRecordingTypes.ts`
- Installation/release channels and Node requirement: `https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/installation.mdx`
- npm stable package: `https://www.npmjs.com/package/@google/gemini-cli` (`0.58.0` current stable on 2026-09-08).

Historical QuaLock prototype contracts consulted as lessons, not implementation authority:

- `2026-09-05-gemini-cli-adapter-design.md`
- `2026-09-05-gemini-bwrap-userns-correction-design.md`
- `2026-09-05-gemini-free-tier-evidence-correction-design.md`

Where historical prototype text conflicts with this mainline design or current upstream 0.58.0 behavior, this document wins.
## Contract clarifications

The exact extension-disable CLI fragment is `-e none`; the resolver must prove the installed version advertises the special `none` behavior before compatibility is accepted.

Agent/reasoning semantic validation belongs in one pure config-contract helper invoked immediately after project config parsing. That helper is reused by every command that loads project configuration, so `provider-default` cannot reach resolver/backend code for Codex, Claude Code, or Antigravity, and Gemini cannot reach resolution with an explicit QuaLock effort.

The Node overlay's exact image digest is an implementation constant selected and tested in the implementation commit. The design intentionally specifies the immutable-digest property rather than embedding a registry digest that would make the design document itself an operational update channel. Changing that digest later requires a reviewed code change and renewed no-auth runtime proof.
The V1 `tools.core` allowlist is exact: `list_directory`, `read_file`, `glob`, `grep_search`, `write_file`, `replace`, and `run_shell_command`. No other built-in tool is enabled. In particular, `web_fetch`, Google web search, memory, codebase-investigator/sub-agent, task-management, skill, browser, and extension/MCP tools are not part of the qualification surface.
Gemini CLI 0.58.0's published `bundle/gemini.js` is a chunked ESM entrypoint whose runtime closure includes nested paths in the published package. Batch #43 therefore adds one generic `AgentSupportTree` primitive rather than hundreds of per-file mounts. The tree root is the exact resolved package root; its fingerprint is SHA-256 over a canonical sorted sequence of package-relative POSIX path plus file SHA-256 for every regular file. Any symlink encountered anywhere under the tree fails closed. The tree is mounted read-only at `/opt/qualock/gemini-package`, so `container_binary_path` is the package-relative entrypoint under that root (for 0.58.0, `/opt/qualock/gemini-package/bundle/gemini.js`). `AgentBinary.path` and `AgentBinary.sha256` continue to identify only the validated `bin.gemini` entrypoint.
