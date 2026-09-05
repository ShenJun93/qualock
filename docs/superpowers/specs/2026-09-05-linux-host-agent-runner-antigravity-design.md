# Linux Host-Agent Runner and Antigravity Adapter Design

**Date:** 2026-09-05
**Batch:** #35
**Status:** Approved, implementation gated on Linux sandbox acceptance

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
work root. The backend never executes an agent directly in the user's source checkout.

Preparation materializes the requested repository/base SHA and runs the same canary setup contract
used by Docker preparation. Host-specific setup differences must fail explicitly rather than be
silently skipped.

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
- use a temporary QuaLock-owned HOME/config directory for settings and scratch state;
- never copy account credential material into that temporary HOME;
- parse Antigravity events into `AgentEvidence`;
- reject malformed or incomplete terminal evidence.

The account session remains owned by Antigravity's Linux secure credential mechanism. QuaLock may
check that authentication is available, but must not enumerate, read, serialize, or fingerprint the
secret itself.

### 6. Permissions are generated by QuaLock

For qualification runs, QuaLock writes the minimum Antigravity settings needed for the attempt.
The generated policy must deny web/MCP/subagent/browser surfaces that the canary does not require,
keep non-workspace file access disabled, enable terminal sandboxing, and permit only the workspace
file/tool operations required by the qualification contract.

No persisted user Antigravity settings are trusted as the security policy. User settings must not be
mutated by a qualification run.

The generated policy must not contain `unsandboxed(...)` allows. If Antigravity asks to escape the
sandbox, headless execution must deny that request and the attempt must be invalid.

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

## Acceptance spike findings — 2026-09-05

The pre-implementation WSL spike did not satisfy the complete acceptance gate, so this design
remains implementation-gated and no production plan may be executed yet.

Verified positive results:

- native Linux `agy 1.1.27` is installed as an ELF binary and authenticated headless execution works
  from the operator's existing Linux HOME;
- `stream-json` exposes init, tool, result, usage, denial, and error data suitable for a parser;
- Antigravity Linux terminal sandbox starts successfully when the outer Bubblewrap probe provides a
  private `/dev` via `--dev /dev`;
- inside that terminal sandbox, workspace files and normal runtime system files are visible;
- a designated file under the operator HOME is invisible to the sandboxed shell;
- direct outbound TCP from the sandboxed shell is blocked;
- the repository and user Antigravity `settings.json` were restored after probing.

Load-bearing failures/blockers:

- Antigravity file tools were able to read a sibling file outside the workspace while
  `allowNonWorkspaceAccess=false`; this setting alone is therefore not an accepted QuaLock boundary;
- changing HOME to a QuaLock-owned temporary directory causes `agy` to require OAuth again;
- adding GNOME Secret Service and a DBus session collection did not migrate the pre-existing
  Antigravity session, and fresh-HOME silent authentication remains unavailable;
- because parent file tools and parent authentication still share the same HOME-visible state, the
  current spike cannot prove that agent file tools are unable to inspect authentication/runtime
  material while the parent process remains authenticated.

Before implementation planning can resume, one of these must be proven with runtime evidence:

1. **Keyring path:** a supported Antigravity login stored in Linux Secret Service survives a fresh
   QuaLock-owned HOME, so parent authentication can be separated from user filesystem state; or
2. **Parent-isolation path:** an outer Linux isolation boundary plus explicit Antigravity permission
   rules prevents all agent file tools from reading parent authentication/runtime state while still
   allowing the parent CLI to authenticate and operate.

The second path must be demonstrated against the exact pinned CLI version. Documentation of
`read_file(...)` deny precedence is not sufficient by itself; QuaLock requires a real negative probe.
The bridge used in this session refused that explicit-deny probe before execution, so no claim is
made about its runtime behavior.

Until one path passes, Batch #35 stays **design-only**. Do not add `LinuxHostQualificationBackend`,
do not route `antigravity@...`, and do not weaken the boundary with `--dangerously-skip-permissions`,
a Windows fallback, copied credentials, API-key substitution, or an unsandboxed retry.
