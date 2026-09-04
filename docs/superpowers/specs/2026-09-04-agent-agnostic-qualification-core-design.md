# Agent-Agnostic Qualification Core Design

**Date:** 2026-09-04
**Batch:** #30
**Status:** Approved

## Context

QuaLock v0.1 proved the qualification loop with Codex only. The next product direction is:

1. genericize the qualification core;
2. add Claude Code as the second coding-agent adapter;
3. add additional agents only after the abstraction survives a real second adapter.

The repository already contains useful generic seams (`AgentBinary`, `AgentCapabilities`,
`QualificationBackend`), but the local execution path still embeds Codex assumptions in the Docker
backend, credential handling, evidence parser, container binary path, and report copy. Adding Claude
Code directly on top of those assumptions would create a large conditional tree instead of a real
adapter boundary.

## Goal

Make the **local baseline/check qualification core** agent-agnostic while preserving current Codex
behavior. Batch #30 adds no new public agent. Its output is a clean adapter contract that Batch #31
can implement for Claude Code without modifying Docker orchestration or qualification policy.

## Non-goals

Batch #30 does **not**:

- add Claude Code, Gemini CLI, or any other agent;
- make `release monitor`, `version bisect`, scheduling, or GitHub PR qualification multi-agent;
- change the baseline lock schema;
- change qualification policy, pairing/interleaving, canary grading, or protected-path policy;
- redesign model configuration;
- change the public `codex@<version>` CLI contract;
- publish, tag, release, or change repository settings.

The Codex-only release/distribution workflows remain explicit and fail closed. They are widened only
after a second local adapter is proven.

## Compatibility requirements

For successful Codex baseline/check runs:

- CLI wording remains the same;
- `baseline.lock` remains schema version 1 and keeps the same fields;
- `baseline.json`, `report.json`, `qualification.json`, and `report.md` keep the same schema/content;
- qualification IDs, run order, verdicts, token usage, and integrity decisions are unchanged;
- Codex continues to use `/opt/qualock/codex`, `CODEX_HOME=/opt/qualock/auth`, a tmpfs auth home,
  and a read-only external auth seed copied into tmpfs with restrictive permissions;
- `codex-code-mode-host` remains mounted read-only when present.

A low-level Docker preparation error may change from “Codex runner” to “agent runner”; this is the
only intentional Codex wording change in the generic infrastructure.

## Architecture

### 1. Normalized agent evidence

Create `src/qualock/evidence/models.py` with the agent-neutral evidence model used by the backend:

```python
@dataclass(frozen=True)
class CommandEvent:
    command: str
    exit_code: int | None = None

@dataclass
class AgentEvidence:
    thread_id: str | None = None
    commands: list[CommandEvent] = field(default_factory=list)
    file_changes: list[str] = field(default_factory=list)
    web_searches: int = 0
    mcp_calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    errors: list[str] = field(default_factory=list)
    unknown_events: list[dict[str, Any]] = field(default_factory=list)
```

Also define `AgentEvidenceError(ValueError)` as the common parse-failure type.

`codex_jsonl.py` remains the Codex format parser, but it returns `AgentEvidence`. Its existing
`CodexEvidenceError` remains available as a subclass of `AgentEvidenceError` so current callers and
tests retain a specific Codex error when needed. `CommandEvent` is imported from the generic model.

The backend never imports `codex_jsonl.py` after this batch.

### 2. Invocation contract owned by the adapter

Extend `src/qualock/agents/base.py` with immutable runtime descriptions:

```python
@dataclass(frozen=True)
class AgentMount:
    host_path: Path
    container_path: str
    mode: Literal["ro", "rw"]

@dataclass(frozen=True)
class AgentInvocation:
    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()
    mounts: tuple[AgentMount, ...] = ()
    tmpfs_mounts: tuple[str, ...] = ()
    bootstrap_copy: tuple[str, str] | None = None
    container_binary_path: str = "/opt/qualock/agent"
```

The generic adapter protocol used by the backend becomes:

```python
class AgentAdapter(Protocol):
    def invocation(
        self,
        binary: AgentBinary,
        *,
        model: str,
        reasoning_effort: str,
        prompt: str,
    ) -> ContextManager[AgentInvocation]: ...

    def parse_evidence(self, stdout: str, stderr: str) -> AgentEvidence: ...
```

The context-manager boundary is deliberate: an adapter may need temporary host files whose lifetime
must cover the Docker run. The backend must not know whether those files contain Codex auth,
Claude credentials, or something else.

### 3. Codex adapter owns Codex runtime details

`CodexAdapter` continues to expose its capability detection and argv construction helpers, but gains:

- constructor input `auth_home: Path | None`;
- `invocation(...)` implementing the generic adapter contract;
- `parse_evidence(stdout, stderr)` delegating to `parse_codex_jsonl`.

`invocation(...)` must:

1. detect the Codex execution capabilities;
2. build the same Codex argv as today;
3. set `container_binary_path="/opt/qualock/codex"`;
4. when `auth_home` is not `None`, request tmpfs `/opt/qualock/auth` and set
   `CODEX_HOME=/opt/qualock/auth`, matching current behavior even if `auth.json` is absent;
5. when `<auth_home>/auth.json` exists, copy it into a temporary host directory;
6. mount that temporary file read-only as `/opt/qualock/auth-seed.json`;
7. request bootstrap copy from the seed to `/opt/qualock/auth/auth.json`;
8. keep the temporary seed alive until the context manager exits, then remove it.

Support binaries remain data on `AgentBinary`. The backend mounts every support binary using its
recorded `container_path`, so this behavior is generic and remains reusable by future agents.

### 4. Docker backend becomes agent-neutral

`DockerQualificationBackend` changes constructor fields from:

- `codex_adapter` -> `agent_adapter`;
- remove `auth_home` entirely.

During `run_attempt`, it must:

1. enter `agent_adapter.invocation(...)`;
2. merge invocation mounts with read-only `AgentBinary.support_binaries` mounts;
3. pass invocation argv/environment/tmpfs/bootstrap/container path to `DockerRunner`;
4. parse output only through `agent_adapter.parse_evidence(state.stdout, state.stderr)`;
5. treat any `AgentEvidenceError` as invalid evidence;
6. preserve the existing exit-code, agent-error, web-search, MCP, protected-path, usage, grader, and
   cleanup behavior exactly.

No Codex name, `CODEX_HOME`, `auth.json`, or Codex parser import remains in `run/backend.py`.

### 5. Docker runner accepts an agent-specific container binary path

`DockerRunner.build_agent_create_argv(...)` and `run_agent(...)` gain an
`agent_container_path` argument. When replacing the host binary, Docker mounts the binary at that
path and rewrites `argv[0]` to that path.

Validation requirements:

- the path is absolute;
- `agent_argv` is non-empty when replacement is enabled;
- existing mount/tmpfs/bootstrap validation remains unchanged.

Codex supplies `/opt/qualock/codex`, preserving current container behavior. The generic default may
be `/opt/qualock/agent` for tests/future adapters.

The Dockerfile bootstrap error becomes agent-neutral: “Qualock agent runner requires bubblewrap in
the runtime image”.

### 6. Reporting accepts agent presentation instead of hard-coding Codex

The report layer must not infer the product name from versions.

Change these APIs to receive a required keyword-only `agent_display_name: str`:

```python
render_markdown(result, *, agent_display_name: str) -> str
render_terminal(result, *, agent_display_name: str) -> str
build_safety_summary(result, display_names, *, agent_display_name: str) -> SafetySummary
write_qualification_artifacts(base_dir, result, *, agent_display_name: str) -> Path
```

`SafetySummary` stores `agent_display_name`, and recommendation text/rendering uses that field.

Codex callers pass `"Codex"`, so successful Codex report text remains byte-for-byte equivalent.
`render_json(result)` remains unchanged and receives no presentation metadata; this prevents an
artifact schema change in a refactor-only batch.

### 7. Command wiring remains Codex-only but no longer injects Codex details into the backend

`commands._default_backend(...)` constructs:

```python
CodexAdapter(auth_home=Path.home() / ".codex" if it exists else None)
```

and passes it as `agent_adapter`.

`execute_check(...)` passes `agent_display_name="Codex"` when writing qualification artifacts.
CLI technical and safety rendering also pass `"Codex"` explicitly. This intentional top-level
Codex wiring is the seam Batch #31 replaces with agent routing; it is not allowed to leak back into
Docker/evidence/report internals.

`parse_agent_spec`, `AgentConfig`, Codex resolver selection, baseline pin naming, release monitor,
version bisect, and GitHub PR qualification remain Codex-only in Batch #30.

## Error handling

- Adapter-specific malformed evidence must raise a subclass of `AgentEvidenceError`.
- Backend converts evidence parse failure into the same invalid-attempt path used today.
- Adapter capability failures propagate as adapter errors exactly as today.
- Temporary credential material is never mounted directly from the user's source credential file.
- Temporary credential material is deleted when invocation context exits, including error paths.
- Container cleanup remains in the backend `finally` block.

## Security invariants

The refactor must not weaken any existing invariant:

- no host credential file is mounted directly into the writable container auth home;
- credential seed mount is read-only;
- credential destination lives on tmpfs with restrictive bootstrap permissions;
- support binaries are read-only;
- web search and MCP activity still invalidate evidence when configured;
- protected-path mutation still invalidates the attempt before grading;
- unparseable evidence fails closed;
- grader execution occurs only after valid agent execution/evidence/integrity checks;
- container state is cleaned up on every attempt path.

## Testing strategy

Use TDD for every production change.

Required focused tests:

1. `AgentEvidence` is the object returned by the Codex parser; malformed Codex JSON raises
   `CodexEvidenceError`, which is an `AgentEvidenceError`.
2. `CodexAdapter.invocation` preserves argv and secure auth behavior, including temporary seed
   lifetime and deletion after context exit.
3. Backend tests use a fake **generic adapter** whose invocation and evidence are already normalized;
   no test backend fixture emits Codex JSON to make the backend work.
4. Backend still rejects agent errors, web search, MCP use, protected-path changes, nonzero exits,
   and malformed adapter evidence before grading.
5. Support binaries remain mounted read-only.
6. Docker runner rewrites `argv[0]` to a supplied container binary path and rejects relative paths.
7. Report tests render both `"Codex"` and a synthetic `"Claude Code"` name, proving presentation is
   injected while Codex output remains unchanged.
8. Existing CLI/storage tests continue to prove current Codex wording and artifact schema.

Final verification:

- full `pytest`;
- Ruff on changed Python/test files;
- strict mypy on `src/qualock`;
- `python -m compileall -q src tests`;
- `git diff --check`;
- focused security/backend/report test subset;
- confirm `release_monitor`, `version_bisect`, and `github_pr` behavior remains Codex-only;
- confirm `pyproject.toml` is unchanged.

## Expected Batch #31 seam

After Batch #30, Claude Code support should require a new resolver/adapter/evidence implementation
plus top-level routing/config/report presentation. It must **not** require edits to Docker
qualification orchestration, qualification policy, or normalized evidence semantics.

That constraint is the practical test that Batch #30 produced a real adapter boundary rather than a
Codex abstraction renamed “agent”.
