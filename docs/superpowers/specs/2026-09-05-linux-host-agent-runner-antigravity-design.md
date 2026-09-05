# Linux Host-Agent Runner and Antigravity Adapter Design

**Date:** 2026-09-05
**Batch:** #35
**Status:** Approved — corrected Linux acceptance gate passed

## Context

QuaLock already has an agent-neutral qualification executor and a Docker-backed qualification
backend used by Codex and Claude Code. Batch #34 is extending that Docker path for Gemini CLI.

Antigravity CLI introduces a different constraint: Google AI Pro account authentication is owned by
the host operating system session rather than a portable API credential that QuaLock should copy
into a container. A Windows feasibility spike proved account-authenticated `agy 1.1.25` headless
execution works, but also showed that Windows host execution is not an acceptable QuaLock security
boundary: non-workspace file access remained possible and AppContainer terminal sandbox behavior
was not reliable enough for certification.

Official Antigravity documentation describes the Linux terminal sandbox as an OS-level namespace
boundary that restricts filesystem visibility, hides host processes, and blocks network by default.
This makes native Linux/WSL the only host-execution candidate for Batch #35.

## Goal

Add a **Linux-only host qualification path** that can qualify Antigravity CLI while preserving the
existing Docker qualification path for Codex, Claude Code, and Gemini CLI.

The design must reuse QuaLock's existing `QualificationBackend` seam rather than refactor the
qualification executor again. Antigravity must use its own Linux account session and must not
require copying Windows Credential Manager material into WSL or Docker.

## Product Architecture

QuaLock keeps three layers:

```text
User surfaces
  QuaLock CLI
  future ChatGPT / Work integration
          |
Execution core
  QualificationExecutor
  +-- DockerQualificationBackend
  |     +-- Codex
  |     +-- Claude Code
  |     `-- Gemini CLI
  `-- LinuxHostQualificationBackend
        `-- Antigravity CLI
```

The ChatGPT/Work integration is intentionally above QuaLock. It is not treated as a coding agent,
is not browser-automated, and is not part of Batch #35 implementation.

## Non-goals

Batch #35 does **not**:

- move Codex, Claude Code, or Gemini CLI out of Docker;
- replace `DockerQualificationBackend`;
- generalize every release/scheduler/GitHub workflow to host agents;
- automate ChatGPT web UI or browser sessions;
- copy, export, inspect, persist, or translate Antigravity account credentials;
- support Windows or macOS host execution;
- use `--dangerously-skip-permissions`;
- rely on Windows AppContainer as a certification boundary;
- publish, push, merge, tag, release, or change repository settings.

## Pre-implementation Linux Acceptance Gate

No production implementation begins until an authenticated WSL/Linux spike proves all of the
following against a pinned Antigravity CLI version:

1. `agy --version`, `agy models`, and a headless print request succeed from WSL using a Linux-owned
   account session.
2. The account session is created by Antigravity's supported login flow in WSL. No Windows
   credential, token database, cookie, keyring file, or browser profile is copied into WSL.
3. A throwaway workspace can be read and modified by the agent.
4. A sibling non-workspace sentinel cannot be read through file tools.
5. A sandboxed shell command can access the workspace and normal runtime system files needed for
   development.
6. A sandboxed shell command cannot read a designated sensitive host sentinel.
7. Direct outbound TCP from the sandbox is blocked unless explicitly allowlisted.
8. No unsandboxed retry is automatically approved.
9. Headless `stream-json` produces enough structured data to identify tool calls, terminal result,
   usage, errors, denied actions, and file operations without scraping prose.
10. The same probes are repeatable after restarting the CLI.

If any load-bearing item fails, Batch #35 remains design-only. QuaLock must not compensate by
weakening host permissions or silently disabling the sandbox.

## Architecture

### 1. Keep `QualificationExecutor` unchanged

`QualificationExecutor` already targets the `QualificationBackend` protocol and does not depend on
Docker-specific details. Batch #35 must preserve that interface and implement a second backend.

This is the primary architectural constraint: host support is an extension at the backend seam, not
a new execution engine and not another agent-name conditional inside the executor.

### 2. Introduce a host-prepared target without leaking Docker types

The current backend protocol returns `PreparedImage`, whose name is Docker-specific even though the
executor only consumes its `digest` for evidence/reporting. Batch #35 should introduce an
agent-neutral prepared-target value and migrate both backends to it without changing policy:

```python
@dataclass(frozen=True)
class PreparedTarget:
    reference: str
    digest: str
```

`DockerQualificationBackend.prepare(...)` may return `PreparedTarget` with the same image reference
and digest it already exposes. `LinuxHostQualificationBackend.prepare(...)` returns a reference to
its isolated materialized workspace and a deterministic digest derived from the source/base/setup
inputs it prepared.

This type migration is mechanical. It must not change run order, attempt policy, baseline schema,
verdicts, or artifact semantics.

### 3. Add `LinuxHostQualificationBackend`

The new backend owns host-native preparation, execution, integrity inspection, grading, and cleanup.
It must implement the same public backend methods:

```python
prepare(canary, qualification_id) -> PreparedTarget
run_attempt(*, canary, prepared, binary, side, repetition) -> AttemptResult
```

Each attempt receives its own isolated working copy under `.qualock/work` or the existing project
work root. The backend never executes an agent directly in the user's source checkout. Each attempt
also receives fresh private Antigravity config/app-data trees and a private outer `/tmp`; profile state
is never reused between repetitions.

Preparation materializes the requested repository/base SHA and runs the same canary setup contract
used by Docker preparation. Host-specific setup differences must fail explicitly rather than be
silently skipped.

### 3a. Canary runtime profile is explicit

Batch #35 extends `RuntimeSpec` with `execution: Literal["container", "linux-host"] = "container"` and makes `image` optional only for `linux-host`. Existing canaries remain container-backed without edits. `DockerQualificationBackend` rejects non-container canaries; `LinuxHostQualificationBackend` rejects non-`linux-host` canaries. This prevents the host backend from silently ignoring a Docker runtime image.

For a `linux-host` canary, setup and grader commands execute in the disposable WSL attempt workspace under QuaLock-owned process control. The canary author is therefore explicitly opting into the host runtime rather than claiming Docker-image equivalence.

### 4. Host execution is Linux-only and fail-closed

`LinuxHostQualificationBackend` must reject non-Linux platforms before preparation or execution.
WSL counts as Linux only when the runtime probes required by this spec pass.

The backend must not provide a Windows fallback and must not invoke `agy.exe` through Windows from
WSL. If Antigravity's Linux sandbox cannot be established, the attempt is invalid; the backend does
not retry unsandboxed.

The backend owns process timeout and process-tree termination. A timed-out agent must not leave a
child process running against the workspace.

### 5. Antigravity adapter owns CLI-specific behavior

Add an Antigravity resolver/adapter parallel to Codex and Claude:

- resolve a pinned native Linux `agy` binary and hash it;
- validate the expected CLI contract before certification;
- build headless `stream-json` invocation with explicit model and reasoning effort;
- disable slash-command/skill expansion where the CLI exposes a supported flag;
- preserve the operator's existing WSL HOME as the source of Antigravity-owned authentication state;
- inject QuaLock-owned config and app-data trees through process-private Bubblewrap mounts rather than changing HOME;
- read-only bind the existing WSL OAuth token file into the private app-data tree without copying, parsing, serializing, fingerprinting, or rewriting it;
- parse Antigravity events into `AgentEvidence`;
- reject malformed or incomplete terminal evidence.

Runtime evidence from `agy 1.1.27` shows that WSL intentionally selects file-based token storage.
Therefore Batch #35 must not force Secret Service or a temporary HOME. Authentication remains owned
by Antigravity under the operator's normal WSL HOME. Inside the Bubblewrap namespace, QuaLock mounts
a private `~/.gemini/config` and private `~/.gemini/antigravity-cli` tree, then read-only binds the
existing `antigravity-oauth-token` into that private app-data tree so auth works without exposing the
rest of Antigravity runtime state as writable configuration.

### 6. Permissions are generated by QuaLock

For qualification runs, QuaLock builds two process-private trees: a config tree containing the
security plugin/hook and import manifest, and an app-data tree containing generated `settings.json`
with terminal sandboxing plus `proceed-in-sandbox`. Bubblewrap mounts both trees only for the agent
process, then read-only binds the existing OAuth token file into the private app-data token path.
The user's real config, plugin manifest, settings, and token file remain unchanged.

The `PreToolUse` hook receives an explicit `QUALOCK_WORKSPACE` environment value owned by QuaLock.
For file tools it resolves target paths with `realpath`, allows targets inside that workspace, and
hard-denies targets outside it. Web, browser, URL, MCP, subagent, and permission-escalation surfaces
are hard-denied by the hook. Runtime-provided `workspacePaths` may be recorded as corroborating
evidence but is not the security authority because headless `agy 1.1.27` was observed to report it
inconsistently across configurations.

Generated settings enable terminal sandboxing and `proceed-in-sandbox`, deny URL/MCP escape surfaces,
and contain no `unsandboxed(...)` allow. User settings are not trusted or mutated. A requested
unsandboxed execution or failed hook is an invalid attempt; there is no unsandboxed retry.

### 7. Reproducible CLI execution

QuaLock certification requires a stable CLI binary. Antigravity includes a background self-updater,
so every qualification process must set:

```text
AGY_CLI_DISABLE_AUTO_UPDATE=true
```

The resolver records the exact `agy --version` output and SHA-256 of the native Linux binary used
for each baseline/candidate pin. A resolved binary must not mutate during an attempt.

Historical-version acquisition is not assumed by this design. Before implementation planning, the
Linux spike must prove a legitimate way to obtain or retain the exact versions QuaLock intends to
compare. If only the currently installed version can be pinned safely, Batch #35 may initially ship
baseline/check support only for explicitly supplied local Antigravity binaries; silent auto-update
or an invented download endpoint is not acceptable.

### 8. Normalize Antigravity `stream-json`

The parser consumes NDJSON events, not assistant prose. It must recognize at least:

- `init`: conversation ID, selected model, permission mode;
- tool `step_update`: tool name, parameters, output/error, state;
- terminal `result`: `SUCCESS`/`ERROR`, response metadata, usage, denied actions;
- usage fields: input, output, thinking/reasoning, and cache-read tokens.

Tool families are normalized into existing `AgentEvidence` counters and command events. Web,
browser, MCP, URL-fetch, subagent, or equivalent network-capable tools must increment integrity
signals rather than disappear as unknown events.

A missing terminal result, malformed JSON, terminal `ERROR`, denied sandbox escape, or unsupported
critical event shape fails closed through `AgentEvidenceError`.

### 9. File changes and grading remain QuaLock-owned evidence

QuaLock must not trust the agent's claim that it edited only certain files. After each attempt the
host backend computes changed paths and patch evidence from the isolated Git working copy using
QuaLock-owned Git/process code.

Protected-path validation uses the same `protected_path_violations(...)` policy as Docker attempts.
The grader runs only after agent execution, parsed evidence, and protected-path checks are valid.

Host grading executes in the isolated attempt workspace under a QuaLock-owned process environment,
not through Antigravity. It receives an explicit timeout and no Antigravity account environment.
Baseline and candidate attempts always start from equivalent clean prepared state; one attempt may
not reuse another attempt's mutated workspace.

### 10. Public routing is explicit

When the Linux acceptance gate passes, the supported local agent set may expand to:

```text
codex@<version>
claude@<version>
antigravity@<version-or-local-pin>
```

Command routing selects the backend by agent execution profile rather than by scattering agent-name
conditionals through qualification logic:

```text
containerized -> DockerQualificationBackend
linux-host    -> LinuxHostQualificationBackend
```

The Antigravity model remains user/config selected. Batch #35 must not hard-code a specific model
into the generic adapter, although real-contract tests may pin a certified model available to the
operator's Google AI Pro account.

## Security invariants

- No Windows credential material is copied into WSL.
- QuaLock never reads or serializes Antigravity account secrets.
- Qualification never runs in the user's source checkout.
- Host-agent execution is Linux-only and sandbox-required.
- `--dangerously-skip-permissions` is forbidden.
- `unsandboxed(...)` allow rules are forbidden in generated qualification policy.
- Non-workspace filesystem access is denied by policy and independently proven by the Linux spike.
- Sandboxed child network is blocked by default; no automatic network allowlist is added.
- Browser, web, URL-fetch, MCP, plugin/skill expansion, and subagent activity are rejected unless a
  later spec explicitly widens the contract.
- Agent timeout kills the full process tree.
- Attempt workspaces are disposable and isolated from each other.
- File-change/protected-path evidence comes from QuaLock-owned Git inspection.
- Grading occurs only after execution/evidence/integrity gates pass.
- Authentication availability may be tested; credential contents may not.
- CLI auto-update is disabled during qualification and binary SHA-256 is recorded.

## Failure handling

The host attempt is invalid when any of these occurs:

- Linux sandbox prerequisites are unavailable;
- Antigravity authentication is unavailable;
- binary version/hash changes unexpectedly;
- headless execution times out or exits non-zero;
- terminal `result` is missing or reports `ERROR`;
- an unsandboxed action is requested or observed;
- evidence parsing fails;
- forbidden web/MCP/browser/subagent activity occurs;
- protected files change;
- workspace inspection cannot prove the post-attempt state.

A failed host attempt never falls back to Windows, Docker credential copying, API-key mode, or an
unsandboxed Antigravity process.

## Testing strategy

Production implementation uses TDD after the Linux acceptance gate is green.

Required unit/integration coverage includes:

1. `PreparedTarget` migration preserves Docker backend behavior.
2. Linux backend rejects non-Linux platforms.
3. Host preparation creates independent clean workspaces for attempts.
4. Antigravity resolver pins version/hash and disables auto-update during execution.
5. Generated settings contain the exact sandbox/permission restrictions and no unsandboxed allow.
6. Parser normalizes successful stream-json, usage, commands, file tools, denied actions, and errors.
7. Parser fails closed on missing/malformed/terminal-error evidence.
8. Host backend detects protected-path mutations from Git state, not model claims.
9. Agent timeouts terminate descendants and clean attempt state.
10. Grader receives no Antigravity credential environment.
11. Real Linux acceptance proves workspace edit, non-workspace denial, sensitive-file denial,
    network block, no sandbox escape, and stable stream-json.
12. Existing Codex/Claude Docker qualification tests remain green without behavior drift.

## Future ChatGPT / Work surface

A later batch may expose QuaLock through a ChatGPT app/plugin/connector or other authenticated user
surface. That layer may trigger existing QuaLock commands and render qualification results for
low-tech users, but it must not become an execution backend and must not automate the ChatGPT web UI.

The intended future flow is:

```text
ChatGPT / Work
    -> explicit QuaLock tool/action
    -> existing QuaLock command/service boundary
    -> QualificationExecutor
    -> selected backend
    -> normalized result/artifacts
```

This future surface must preserve QuaLock's approval boundaries for pushes, merges, releases, and
other external side effects. It is deliberately outside Batch #35 implementation and testing.

## Rollout

1. Commit this design only.
2. Install native Linux `agy` in WSL from Google's supported installer.
3. Authenticate once in WSL using Google's supported account flow; do not migrate Windows secrets.
4. Run the pre-implementation Linux acceptance gate in throwaway directories.
5. If the gate fails, record the blocker and stop Batch #35 before production code.
6. If the gate passes, write an implementation plan and execute it task-by-task with TDD and
   independent reviews.
7. Run full Linux QuaLock gates and a real Antigravity contract before considering the batch done.

## Acceptance criteria

Batch #35 is complete only when:

- all pre-implementation Linux security probes pass;
- existing Docker-backed agents retain their current behavior;
- Antigravity can baseline/check through the Linux host backend without credential copying;
- host execution is sandbox-required and fail-closed;
- normalized evidence and Git-owned integrity checks are complete;
- real authenticated Antigravity qualification passes on the pinned Linux CLI/model contract;
- full tests, Ruff, strict mypy, compileall, diff-check, and independent whole-branch review pass;
- no push, PR, merge, tag, release, or publication occurs without separate authorization.

## Corrected acceptance spike findings — 2026-09-05

The Linux acceptance gate is now green for the architecture in this design. Earlier fresh-HOME / Secret Service experiments are superseded by runtime evidence showing that WSL `agy 1.1.27` intentionally selects file-based token storage. Batch #35 preserves the normal authenticated WSL HOME and isolates QuaLock policy/config instead.

Verified runtime results:

- native Linux `agy 1.1.27` is an ELF binary; authenticated Google AI Pro headless execution succeeds;
- WSL runtime logs explicitly select file-based token storage, so no keyring migration or repeated OAuth is required;
- a QuaLock `PreToolUse` hook loaded through an Antigravity plugin and hard-blocked tool calls before execution;
- with `QUALOCK_WORKSPACE` supplied by QuaLock, `view_file` and `write_to_file` succeeded inside the workspace and were hard-denied outside it;
- `search_web` and `invoke_subagent` were hard-denied by the same hook before execution;
- a process-private Bubblewrap mount of generated `~/.gemini/config` loaded the hook while leaving the user's real `import_manifest.json` unchanged (`imports: null`);
- a process-private app-data mount with generated `settings.json` plus a read-only bind of the existing OAuth token preserved silent authentication, reported `permission_mode=proceed-in-sandbox`, and executed a sandboxed shell command successfully;
- with a fresh config/app-data profile, outer private `/tmp`, and workspace rebound at `/tmp/qualock-workspace`, the inner shell ran from the expected cwd while both a HOME sentinel and a host `/tmp` sentinel were invisible and outbound TCP remained blocked;
- Antigravity terminal sandbox with `proceed-in-sandbox` read workspace/system runtime files, could not read a HOME sentinel (`FileNotFoundError`), and blocked direct outbound TCP (`OSError`);
- `stream-json` exposed init/tool/result/usage/error evidence sufficient for a fail-closed parser;
- no `--dangerously-skip-permissions`, Windows fallback, API-key substitution, copied credentials, or unsandboxed retry was used.

Runtime/docs discrepancies that implementation must encode rather than paper over:

- WSL token storage differs from the generic Linux keyring documentation; trust runtime contract tests for the pinned CLI;
- plugin staging on `agy 1.1.27` uses `~/.gemini/config/plugins`, despite some docs describing `~/.gemini/antigravity-cli/plugins`;
- workspace `.agents/hooks.json` was not discovered in the headless probe (`loaded 0 named hooks`); the accepted contract uses a generated plugin in the private config tree;
- `workspacePaths` was empty in one headless configuration, so the security hook uses QuaLock's explicit `QUALOCK_WORKSPACE` as authority and treats runtime `workspacePaths` only as corroboration.

These findings unlock implementation planning. Any implementation that changes the pinned CLI or injection mechanism must rerun these negative probes before certification.
