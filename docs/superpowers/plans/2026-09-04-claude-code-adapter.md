# Claude Code Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Claude Code as the second agent for local QuaLock baseline/check without changing the Batch #30 generic Docker qualification backend.

**Architecture:** Add a Claude native-binary resolver, a Claude adapter that owns isolated settings/credentials/invocation, and a stream-json evidence parser. Extend only top-level config/command/CLI routing to select Codex or Claude; release/distribution workflows remain Codex-only.

**Tech Stack:** Python 3.11+, Pydantic, Typer, npm package metadata/install, Docker runner contracts from Batch #30, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-04-claude-code-adapter-design.md`

## Global Constraints

- Batch #31 is stacked on Batch #30 exact head `24211ab499ed431aa8f9d13752957c2355586a37`.
- Do not add Claude-specific logic to `src/qualock/run/backend.py` or change qualification policy. A generic runtime-dependency forwarder is permitted for the discovered Claude sandbox prerequisite.
- Default config and all existing Codex behavior remain Codex.
- Claude support is local `baseline`/`check` only.
- No original Claude credential file is mounted directly and no secret is passed through Docker environment metadata.
- Exact Claude runs set `DISABLE_AUTOUPDATER=1` so the resolved version cannot self-update.
- `release_monitor`, `version_bisect`, `scheduler`, and `github_pr` remain unchanged and Codex-only.
- Use TDD for every production change and commit each task separately.

---

### Task 1: Resolve exact Claude native binaries

**Files:**
- Create: `src/qualock/agents/claude_resolver.py`
- Create: `tests/unit/test_claude_resolver.py`

**Interfaces:**
- Produces: `ClaudeResolveError(RuntimeError)`.
- Produces: `ClaudeResolver(cache_root: Path, *, npm_executable: str = "npm", machine: str | None = None)`.
- Produces: `ClaudeResolver.latest_version() -> str`.
- Produces: `ClaudeResolver.resolve(requested_version: str) -> AgentBinary`.

- [ ] **Step 1: Write resolver tests first**

Tests must fake npm and prove x86_64 and arm64 direct native package paths, exact version pinning, `latest`, cache reuse, SHA-256, unsupported architecture, invalid version, registry timeout/nonzero, and missing binary after install.

Expected x86_64 path:

```python
cache / "agents/claude/2.1.260/node_modules/@anthropic-ai/claude-code-linux-x64/claude"
```

Expected install argv contains:

```python
[
    npm,
    "install",
    "--prefix", str(prefix),
    "--no-save",
    "@anthropic-ai/claude-code-linux-x64@2.1.260",
]
```

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_claude_resolver.py
```

Expected: import failure because `qualock.agents.claude_resolver` does not exist.

- [ ] **Step 3: Implement the minimal resolver**

Use the existing semver regex shape from `agents/resolver.py`. Platform map:

```python
{
    "x86_64": "@anthropic-ai/claude-code-linux-x64",
    "amd64": "@anthropic-ai/claude-code-linux-x64",
    "arm64": "@anthropic-ai/claude-code-linux-arm64",
    "aarch64": "@anthropic-ai/claude-code-linux-arm64",
}
```

`latest_version` queries `npm view @anthropic-ai/claude-code version`. `resolve` installs the direct platform package and returns `AgentBinary(name="claude", ...)` with no support binaries.

- [ ] **Step 4: Run focused pytest/Ruff/mypy**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_claude_resolver.py
/tmp/qualock-static-22-final/bin/ruff check src/qualock/agents/claude_resolver.py tests/unit/test_claude_resolver.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/agents/claude_resolver.py
```

- [ ] **Step 5: Commit**

```bash
git add src/qualock/agents/claude_resolver.py tests/unit/test_claude_resolver.py
git commit -m "feat: resolve Claude Code binaries"
```

---

### Task 2: Normalize Claude stream-json evidence

**Files:**
- Create: `src/qualock/evidence/claude_stream_json.py`
- Create: `tests/unit/test_claude_stream_json.py`

**Interfaces:**
- Produces: `ClaudeEvidenceError(AgentEvidenceError)`.
- Produces: `parse_claude_stream_json(lines: Iterable[str]) -> AgentEvidence`.

- [ ] **Step 1: Write parser tests first**

Use inline JSONL fixtures covering:

```python
{"type":"system","subtype":"init","session_id":"s1"}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"pytest -q"}}]}}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"src/app.py"}}]}}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"WebSearch","input":{}}]}}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"mcp__server__tool","input":{}}]}}
{"type":"result","subtype":"success","is_error":false,"usage":{"input_tokens":10,"cache_read_input_tokens":4,"output_tokens":3},"permission_denials":[]}
```

Also test non-success result, permission denial, malformed JSON, non-object JSON, and unknown top-level event retention.

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_claude_stream_json.py
```

- [ ] **Step 3: Implement minimal parser**

Parse only fields QuaLock needs. Usage comes from final `result` events only. Do not accumulate assistant message usage. `WebFetch` and `WebSearch` both increment `web_searches`; `mcp__` prefix increments `mcp_calls`; Bash creates `CommandEvent`; Edit/Write record paths when available.

- [ ] **Step 4: Run focused gates**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_claude_stream_json.py
/tmp/qualock-static-22-final/bin/ruff check src/qualock/evidence/claude_stream_json.py tests/unit/test_claude_stream_json.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/evidence/claude_stream_json.py
```

- [ ] **Step 5: Commit**

```bash
git add src/qualock/evidence/claude_stream_json.py tests/unit/test_claude_stream_json.py
git commit -m "feat: parse Claude Code evidence"
```

---

### Task 3: Build isolated Claude invocation and credential lifecycle

**Files:**
- Create: `src/qualock/agents/claude.py`
- Create: `tests/unit/test_claude_adapter.py`

**Interfaces:**
- Produces: `ClaudeAdapter(auth_home: Path | None = None)` implementing `AgentAdapter`.
- Produces: `ClaudeAdapter.invocation(...) -> ContextManager[AgentInvocation]`.
- Produces: `ClaudeAdapter.parse_evidence(stdout: str, stderr: str) -> AgentEvidence`.

- [ ] **Step 1: Write adapter RED tests**

Assert invocation:

```python
assert invocation.container_binary_path == "/opt/qualock/claude"
assert invocation.environment == (
    ("CLAUDE_CONFIG_DIR", "/opt/qualock/claude-home"),
    ("DISABLE_AUTOUPDATER", "1"),
)
assert invocation.tmpfs_mounts == ("/opt/qualock/claude-home",)
```

Assert argv includes `-p`, `--safe-mode`, `--no-session-persistence`, `--output-format stream-json`, `--permission-mode dontAsk`, `--permission-prompts none`, configured model/effort, minimal tools, empty strict MCP config, explicit settings path, and prompt.

Credential test creates `<auth_home>/.credentials.json`, verifies the mounted seed is a temporary copy rather than the original, mode is `ro`, bootstrap target is `/opt/qualock/claude-home/.credentials.json`, contents match, and temporary files disappear after the context exits.

Missing-credential test verifies settings + tmpfs remain but no credential seed/bootstrap is added.

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_claude_adapter.py
```

- [ ] **Step 3: Implement adapter**

Use one `TemporaryDirectory` per invocation. Always create `settings.json` containing:

```python
{
    "sandbox": {
        "enabled": True,
        "failIfUnavailable": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
    }
}
```

Mount settings read-only at `/opt/qualock/claude-settings.json`. If credentials exist, copy them into the same temp directory and mount only the copy. Delegate evidence parsing to `parse_claude_stream_json(stdout.splitlines())`.

- [ ] **Step 4: Run focused gates**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_claude_adapter.py tests/unit/test_claude_stream_json.py
/tmp/qualock-static-22-final/bin/ruff check src/qualock/agents/claude.py tests/unit/test_claude_adapter.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/agents/claude.py
```

- [ ] **Step 5: Commit**

```bash
git add src/qualock/agents/claude.py tests/unit/test_claude_adapter.py
git commit -m "feat: add isolated Claude Code adapter"
```

---

### Task 4: Route local qualification by configured agent

**Files:**
- Modify: `src/qualock/config/models.py`
- Modify: `src/qualock/commands.py`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_commands.py`

**Interfaces:**
- `AgentConfig.name` becomes `Literal["codex", "claude"]` with default `codex`.
- `parse_agent_spec(spec) -> tuple[str, str]` accepts only Codex/Claude exact names.
- Add private display-name helper returning `Codex` or `Claude Code`.
- Default resolver/backend factories select by agent name.

- [ ] **Step 1: Write routing/config RED tests**

Add tests proving:

```python
parse_agent_spec("claude@2.1.260") == ("claude", "2.1.260")
QualockConfig.model_validate({"agent":{"name":"claude"}, "model":{"id":"sonnet"}}).agent.name == "claude"
```

Add baseline test using injected fake resolver/backend that writes `lock.agent.name == "claude"`. Add mismatch tests showing config `codex` + CLI `claude@...`, candidate Claude + Codex baseline, and config/baseline mismatch all raise `CommandError` before resolver calls.

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_config.py tests/unit/test_commands.py
```

- [ ] **Step 3: Implement routing**

Import `ClaudeAdapter` and `ClaudeResolver` only in command routing. Keep `_default_resolver` and `_default_backend` agent-aware. `execute_baseline` pins the parsed name; `execute_check` validates candidate/config/baseline agent equality before resolving binaries and writes artifacts with the selected display name.

Default Claude auth home:

```python
auth_home = Path.home() / ".claude"
ClaudeAdapter(auth_home=auth_home if auth_home.exists() else None)
```

Do not modify release-monitor/bisect/GitHub PR code.

- [ ] **Step 4: Run focused gates**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_config.py tests/unit/test_commands.py tests/unit/test_claude_resolver.py tests/unit/test_claude_adapter.py
/tmp/qualock-static-22-final/bin/ruff check src/qualock/config/models.py src/qualock/commands.py tests/unit/test_config.py tests/unit/test_commands.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/config/models.py src/qualock/commands.py
```

- [ ] **Step 5: Commit**

```bash
git add src/qualock/config/models.py src/qualock/commands.py tests/unit/test_config.py tests/unit/test_commands.py
git commit -m "feat: route local qualification by agent"
```

---

### Task 5: Render Claude CLI output without changing Codex output

**Files:**
- Modify: `src/qualock/cli.py`
- Modify: `tests/unit/test_cli.py`

**Interfaces:**
- Safety and technical render helpers receive/derive agent display name from the CLI agent spec.
- Baseline success copy uses the baseline lock agent display name.

- [ ] **Step 1: Write CLI RED tests**

Add Claude check tests using `sample_result()` and candidate `claude@2.1.260`. Assert output contains `Claude Code 0.150.0 -> 0.151.0` and recommendations use `Claude Code`.

Keep `test_check_easy_output_is_exactly_preserved` unchanged as the Codex byte-compatibility guard.

Add Claude baseline output test asserting `Baseline pinned: Claude Code 2.1.260` from a fake lock.

- [ ] **Step 2: Verify RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_cli.py
```

- [ ] **Step 3: Implement minimal CLI presentation routing**

Use the parsed agent name only for display selection. Monitor paths remain hard-coded Codex because the monitor command itself remains Codex-only.

- [ ] **Step 4: Run focused CLI/report gates**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_cli.py tests/unit/test_report.py tests/unit/test_safety_report.py tests/unit/test_storage.py
/tmp/qualock-static-22-final/bin/ruff check src/qualock/cli.py tests/unit/test_cli.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/cli.py
```

- [ ] **Step 5: Commit**

```bash
git add src/qualock/cli.py tests/unit/test_cli.py
git commit -m "feat: render Claude qualification output"
```

---

### Task 6: Final regression, compatibility, and scope gate

**Files:**
- Production changes are limited to fixes for invariants discovered during the final gate.
- The discovered Claude Linux sandbox prerequisite requires a generic runtime-dependency contract in `agents/base.py`, forwarding in `run/backend.py`, and dependency-aware bootstrap in `run/docker.py`.
- Add regression tests for every discovered invariant.

- [ ] **Step 0: Lock discovered Claude sandbox runtime prerequisites**

TDD coverage must prove `ClaudeAdapter` requests `AgentRuntimeDependency(command="socat", apt_package="socat=1.7.4.4-2")`, Claude settings include `enableWeakerNestedSandbox=true`, backend prepare forwards dependencies generically, Docker prepare installs pinned socat only when requested, and the default/Codex bootstrap remains bubblewrap-only.

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_claude_adapter.py \
  tests/unit/test_docker_backend.py \
  tests/unit/test_docker_commands.py
```

- [ ] **Step 1: Full pytest**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q -rs
```

Expected: at least 778 tests plus new Batch #31 tests, all passing.

- [ ] **Step 2: Focused Claude/local qualification gate**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_claude_resolver.py \
  tests/unit/test_claude_stream_json.py \
  tests/unit/test_claude_adapter.py \
  tests/unit/test_config.py \
  tests/unit/test_commands.py \
  tests/unit/test_cli.py \
  tests/unit/test_docker_backend.py \
  tests/unit/test_docker_commands.py \
  tests/unit/test_report.py \
  tests/unit/test_safety_report.py \
  tests/unit/test_storage.py
```

- [ ] **Step 3: Static gates**

```bash
/tmp/qualock-static-22-final/bin/ruff check <all changed Python/test files>
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock
/home/pacmap/qualock-exp/.venv/bin/python -m compileall -q src tests
git diff --check 24211ab499ed431aa8f9d13752957c2355586a37...HEAD
```

- [ ] **Step 4: Scope proof**

```bash
git diff --exit-code 24211ab499ed431aa8f9d13752957c2355586a37...HEAD -- \
  src/qualock/qualification \
  src/qualock/release_monitor \
  src/qualock/version_bisect \
  src/qualock/scheduler \
  src/qualock/github_pr
! grep -nE 'Claude|claude|socat' src/qualock/run/backend.py
```

Review the `run/backend.py` diff and require that its only semantic change is forwarding `agent_adapter.runtime_dependencies` to generic Docker preparation. Confirm the default Docker bootstrap contains no socat and Codex-only guards still exist in release monitor, version bisect, and GitHub PR code.

- [ ] **Step 5: Verify Codex compatibility**

Run the existing exact-output CLI test and compare `render_json`/qualification artifact schema against Batch #30. No new agent presentation field may appear in `QualificationResult` or report JSON.

- [ ] **Step 6: Independent whole-branch review**

Review exact diff `24211ab...HEAD` against the design spec. Fix all Critical/Important findings and rerun the full gate after any production change.
