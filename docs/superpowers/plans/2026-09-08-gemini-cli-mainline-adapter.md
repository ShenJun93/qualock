# Gemini CLI Mainline Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Gemini CLI 0.58.0+ as QuaLock's fourth local qualification agent on current main without changing qualification policy, grading, historical analysis, pricing mathematics, or the existing Codex/Claude/Antigravity execution contracts.

**Architecture:** Keep `DockerQualificationBackend` agent-generic. Add one generic digest-pinned Node runtime-overlay primitive, then implement Gemini-owned resolver, stream-json parser, isolated invocation adapter, and mainline routing. The parent Gemini process may reach the model API, while every model-launched shell command is intercepted through a QuaLock-owned Bubblewrap wrapper with fresh user/network namespaces and explicit exit-code evidence.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, Docker/BuildKit, npm, Node.js 22 Bookworm, Bubblewrap, pytest, Ruff, strict mypy.

**Spec:** `docs/superpowers/specs/2026-09-08-gemini-cli-adapter-design.md`

## Global Constraints

- Branch: `feat/gemini-cli-mainline-adapter`; base main: `a98374d5c161ef96272e0a94a5f7584511e7f7f9`.
- Minimum validated Gemini CLI version: `0.58.0`; `latest` means npm stable resolved to an exact version before execution.
- Gemini automation auth: non-empty `GEMINI_API_KEY` only. No OAuth cache, ADC, Vertex AI, `GOOGLE_API_KEY`, service-account JSON, or interactive login state.
- Gemini reasoning effort: exactly `provider-default`. Codex, Claude Code, and Antigravity remain `low|medium|high|xhigh` only.
- Node overlay: `node:22.23.2-bookworm@sha256:8a34c4ab3ea2c5cd194f07e317b2a8f09461d3c8b05c4e34c8ccd56d56024c4d`, source `/usr/local`, destination `/opt/qualock/node-runtime`.
- `GEMINI_API_KEY` enters only through existing `stdin_secret_env`; it must never appear in Docker create argv, image/container metadata, settings files, mounts, evidence, git diff, or reports.
- `/opt/qualock/gemini-home` is ephemeral; `/workspace/.gemini` is hidden behind a QuaLock-owned read-only empty bind mount during the agent phase only.
- Gemini parent network remains available; every `run_shell_command` child must enter Bubblewrap with `--unshare-user --unshare-net --bind / / --dev /dev` and no unsandboxed fallback.
- Extensions are disabled with the exact Gemini CLI special selector `-e none`; user/project extensions, hooks, skills, MCP, web/browser tools, local `.env`, and `GEMINI.md` context must not participate.
- No new Python dependency, dependency install outside the resolver's own pinned npm cache work, authenticated Gemini provider run, push, PR, merge, tag, release, or publish without explicit user authorization.
- Existing `qualock history` public output stays byte-for-byte compatible; cost/pricing remains advisory and must never affect PASS/WARN/BLOCK/INCOMPLETE.
- Release monitor, scheduler, version bisect, and GitHub PR qualification remain unsupported for Gemini in Batch #43; Batch #44 owns that parity.
- Authenticated Gemini acceptance is optional and separately authorized. Never fabricate a real transcript fixture when no authorized provider run exists.

---

## File Structure

- `src/qualock/agents/base.py`: generic immutable `AgentRuntimeOverlay` contract and adapter protocol property.
- `src/qualock/run/backend.py`: forwards runtime overlays without agent-name branching.
- `src/qualock/run/docker.py`: validates overlays and renders deterministic multi-stage Dockerfiles.
- `src/qualock/agents/gemini_resolver.py`: exact/latest npm resolution, cache integrity, bin-entrypoint and shell-interception contract checks.
- `src/qualock/evidence/gemini_stream_json.py`: strict Gemini 0.58.0 protocol normalization.
- `src/qualock/agents/gemini.py`: API-key selection, isolated settings/home/project state, Node overlay, Bubblewrap wrapper, invocation construction.
- `src/qualock/config/models.py` + `src/qualock/config/io.py`: add `gemini`/`provider-default` values and one pure semantic validator invoked immediately after model validation.
- `src/qualock/commands.py` + `src/qualock/cli.py`: local baseline/check/doctor/display routing only.
- `src/qualock/pricing/resolve.py` + `src/qualock/pricing/sidecar.py`: distinguish Gemini CLI from Antigravity while mapping both to Google provider; Gemini unknown models fail pricing closed.
- Gemini-focused tests live in `tests/unit/test_gemini_*.py`; generic regression tests prove no non-Gemini behavior changes.
- `README.md`, `ROADMAP.md`, and the canonical spec are documentation-only post-CI changes in the final task.

## Execution Discipline

- Use `superpowers:subagent-driven-development` for execution. One fresh implementer owns one task at a time; no parallel branch writers.
- After every task commit, run fresh controller verification and dispatch a fresh read-only reviewer on the exact task BASE..HEAD range.
- Critical/Important findings require a fresh scoped fixer and fresh exact-head re-review before the next task. Minor findings are ledgered for the final whole-branch review.
- Implementers/reviewers write reports under `.superpowers/sdd/2026-09-08-gemini-cli-mainline-adapter/`; controller owns `progress.md`.
- Controller never writes production fixes directly. No authenticated Gemini provider runtime is used by coding/reviewer subagents.

### Task 1: Add Generic Digest-Pinned Runtime Overlays

**Files:**
- Modify: `src/qualock/agents/base.py`
- Modify: `src/qualock/agents/codex.py`
- Modify: `src/qualock/agents/claude.py`
- Modify: `src/qualock/agents/antigravity.py`
- Modify: `src/qualock/run/backend.py`
- Modify: `src/qualock/run/docker.py`
- Test: `tests/unit/test_docker_backend.py`
- Test: `tests/unit/test_docker_commands.py`
- Regression: `tests/unit/test_codex_adapter.py`
- Regression: `tests/unit/test_claude_adapter.py`
- Regression: `tests/unit/test_antigravity_adapter.py`

**Interfaces:**
- Produces `AgentRuntimeOverlay(image: str, source_path: str, destination_path: str, validation_command: tuple[str, ...] = ())`.
- Extends `AgentAdapter` with `runtime_overlays -> tuple[AgentRuntimeOverlay, ...]`.
- Extends `DockerRunner.prepare(..., runtime_overlays: Sequence[AgentRuntimeOverlay] = ())`.
- Existing adapters return `()`; no-overlay Dockerfile text remains unchanged.

- [ ] **Step 1: Write failing generic overlay tests**

```python
def test_prepare_copies_digest_pinned_runtime_overlay(tmp_path: Path) -> None:
    overlay = AgentRuntimeOverlay(
        image="node:22.23.2-bookworm@sha256:" + "a" * 64,
        source_path="/usr/local",
        destination_path="/opt/qualock/node-runtime",
        validation_command=("/opt/qualock/node-runtime/bin/node", "--version"),
    )
    runner.prepare(source, spec, image_tag="prepared", runtime_overlays=(overlay,))
    text = seen_dockerfile()
    assert "FROM node:22.23.2-bookworm@sha256:" in text
    assert "AS qualock-overlay-0" in text
    assert "COPY --from=qualock-overlay-0 /usr/local /opt/qualock/node-runtime" in text
    assert "RUN /opt/qualock/node-runtime/bin/node --version" in text
```

Add explicit negative tests for a floating image, non-absolute source/destination, and a non-absolute validation executable. Add a snapshot/regression assertion that `runtime_overlays=()` produces exactly the pre-Task-1 Dockerfile bytes.

- [ ] **Step 2: Run RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_docker_commands.py tests/unit/test_docker_backend.py tests/unit/test_codex_adapter.py tests/unit/test_claude_adapter.py tests/unit/test_antigravity_adapter.py
```

Expected: failures because the overlay type/property/prepare argument do not exist.
- [ ] **Step 3: Implement the minimal generic overlay contract**

```python
@dataclass(frozen=True)
class AgentRuntimeOverlay:
    image: str
    source_path: str
    destination_path: str
    validation_command: tuple[str, ...] = ()
```

Add `runtime_overlays` to the adapter protocol. In `DockerRunner.prepare`, validate all overlay inputs before writing the Dockerfile; emit deterministic `qualock-overlay-{index}` stages before the final canary `FROM`, copy each overlay immediately after `COPY . /workspace`, and shell-quote the validation command. Do not alter existing dependency installation or setup-command order.

- [ ] **Step 4: Forward overlays and keep existing adapters empty**

```python
return self.docker_runner.prepare(
    source_dir,
    canary,
    image_tag=f"qualock-prepared-{key}",
    runtime_dependencies=self.agent_adapter.runtime_dependencies,
    runtime_overlays=self.agent_adapter.runtime_overlays,
)
```

Codex, Claude, and Antigravity return `()` explicitly. Update test fakes implementing `AgentAdapter` so strict typing remains sound.

- [ ] **Step 5: Run GREEN + static checks**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_docker_commands.py tests/unit/test_docker_backend.py tests/unit/test_codex_adapter.py tests/unit/test_claude_adapter.py tests/unit/test_antigravity_adapter.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/agents/base.py src/qualock/run/backend.py src/qualock/run/docker.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock
```

Expected: all focused tests pass and static checks exit 0.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/qualock/agents/base.py src/qualock/agents/codex.py src/qualock/agents/claude.py src/qualock/agents/antigravity.py src/qualock/run/backend.py src/qualock/run/docker.py tests/unit/test_docker_backend.py tests/unit/test_docker_commands.py tests/unit/test_codex_adapter.py tests/unit/test_claude_adapter.py tests/unit/test_antigravity_adapter.py
git commit -m "feat: add generic runtime overlays"
```

### Task 2: Resolve and Validate the Pinned Gemini CLI

**Files:**
- Create: `src/qualock/agents/gemini_resolver.py`
- Create: `tests/unit/test_gemini_resolver.py`

**Interfaces:**
- Produces `GeminiResolveError(RuntimeError)`.
- Produces `GeminiResolver(cache_root: Path, *, npm_executable: str = "npm", node_executable: str = "node")`.
- Produces `.latest_version() -> str` and `.resolve(requested_version: str) -> AgentBinary`.
- `AgentBinary.path` is the validated installed `package.json` `bin.gemini` entrypoint; `AgentBinary.sha256` is SHA-256 of that executable file, preserving current core digest semantics.
- [ ] **Step 1: Write resolver RED tests**

Add tests proving all of these independently:

```python
def test_resolves_exact_installed_bin_entrypoint(tmp_path: Path) -> None:
    resolver = GeminiResolver(tmp_path / "cache", npm_executable=str(fake_npm), node_executable=str(fake_node))
    binary = resolver.resolve("0.58.0")
    assert binary.name == "gemini"
    assert binary.version == "0.58.0"
    assert binary.path.name == "gemini.js"
    assert binary.sha256 == hashlib.sha256(binary.path.read_bytes()).hexdigest()
```

- `latest` queries `npm view @google/gemini-cli version`, then resolves/cache-keys the returned exact stable version.
- `0.57.0` fails with a minimum-version error before install.
- host Node `v19.x` fails with a Node >=20 error.
- cache reuse works after replacing `npm_executable` with a nonexistent path.
- package metadata must still pin exact `@google/gemini-cli` version on reuse.
- `package.json` missing/non-string `bin.gemini`, symlink escape, executable mutation, malformed npm metadata, and npm failure all fail closed.
- reported CLI version must exactly match the requested resolved version.
- help must advertise `--prompt`, `--output-format`, `--model`, `--approval-mode`, `--sandbox`, `--extensions`/`-e`, and `--version` option synopses.
- shell-interception source validation fails unless the installed bundle proves Linux shell selection is PATH-resolved `bash` rather than absolute `/bin/bash`.
- [ ] **Step 2: Write install-policy and environment-scrub tests**

The fake npm log must prove exact dependency install behavior:

```text
npm install --package-lock-only --ignore-scripts --no-audit --no-fund @google/gemini-cli@0.58.0
npm ci --ignore-scripts --no-audit --no-fund --omit=dev --omit=optional
```

Allow equivalent argument ordering, but require every flag. Assert registry/proxy variables may pass through while these are removed from npm/node probe environments: `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_PROJECT_ID`, `GOOGLE_CLOUD_LOCATION`, `GEMINI_CLI_HOME`, and Gemini system-settings variables.

- [ ] **Step 3: Run RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_gemini_resolver.py
```

Expected: import failure because `gemini_resolver.py` does not exist.

- [ ] **Step 4: Implement exact resolution/cache layout**

Use `<cache>/agents/gemini/<version>/` with a generated private package manifest. Resolve `latest` before entering this path; never cache under the literal `latest`. Read `node_modules/@google/gemini-cli/package.json`, resolve `bin.gemini` relative to that package root, require the resolved path to stay inside the package root, and hash only the validated executable file for `AgentBinary.sha256`.

Use isolated probe HOME/USERPROFILE and run the installed entrypoint as:

```python
[node_executable, str(entrypoint), "--version"]
[node_executable, str(entrypoint), "--help"]
```
- [ ] **Step 5: Implement shell-interception contract validation**

Scan regular JavaScript files under the installed package bundle and require one implementation unit to prove all three behaviors used by the reviewed 0.58.0 runtime contract: Linux chooses executable `bash`, that executable is passed through the upstream executable resolver, and the resolver searches `process.env.PATH` for non-absolute commands. Do not patch/vendor Gemini CLI. Cache reuse reruns this source-contract check without registry access.

Compute the executable SHA before and after version/help/source validation; if it changes, raise `GeminiResolveError("Gemini executable changed during contract validation")`.

- [ ] **Step 6: Run GREEN + static checks**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_gemini_resolver.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/agents/gemini_resolver.py tests/unit/test_gemini_resolver.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/qualock/agents/gemini_resolver.py tests/unit/test_gemini_resolver.py
git commit -m "feat: resolve pinned Gemini CLI"
```

### Task 3: Normalize Gemini Stream-JSON Evidence

**Files:**
- Create: `src/qualock/evidence/gemini_stream_json.py`
- Create: `tests/unit/test_gemini_stream_json.py`
- Optional only after authorized authenticated acceptance: `tests/fixtures/gemini/stream_json_real_0_58_0.jsonl`
**Interfaces:**
- Produces `GeminiEvidenceError(AgentEvidenceError)`.
- Produces `parse_gemini_stream_json(lines: Iterable[str]) -> AgentEvidence`.
- Recognizes `init`, `message`, `tool_use`, `tool_result`, `error`, `result`; unknown event types are preserved in `unknown_events`.
- Shell exit status is trusted only from exactly one standalone `QUALOCK_GEMINI_EXIT_CODE=<0..255>` marker in the matching tool result.

- [ ] **Step 1: Write protocol-conformance RED tests**

Use clearly synthetic fixtures named as such; do not label them real transcripts.

```python
def test_parses_shell_marker_and_terminal_usage() -> None:
    evidence = parse_gemini_stream_json([
        line({"type":"init","session_id":"s1","model":"gemini-3.5-flash"}),
        line({"type":"tool_use","tool_name":"run_shell_command","tool_id":"c1","parameters":{"command":"pytest -q"}}),
        line({"type":"tool_result","tool_id":"c1","status":"success","output":"ok\nQUALOCK_GEMINI_EXIT_CODE=0\n"}),
        line({"type":"result","status":"success","stats":{"input_tokens":12,"output_tokens":5,"cached":3,"input":9,"duration_ms":10,"tool_calls":1,"models":{}}}),
    ])
    assert evidence.thread_id == "s1"
    assert [(x.command, x.exit_code) for x in evidence.commands] == [("pytest -q", 0)]
    assert (evidence.input_tokens, evidence.cached_input_tokens, evidence.output_tokens) == (12, 3, 5)
```

Add marker cases for `0`, `1`, `7`, `125`, `255`; missing, duplicate, malformed, or out-of-range markers must append an evidence error and leave exit code unknown. `tool_result.status="success"` alone must never imply exit code 0.
- [ ] **Step 2: Add parser safety/classification tests**

Cover these independently:

- malformed JSON/non-object line raises with line number;
- missing/duplicate terminal `result` raises;
- successful result requires non-negative integer `input_tokens`, `output_tokens`, `cached`, `input`, `duration_ms`, `tool_calls`; bools are rejected as integers;
- `models`, when present, must be an object; structurally valid `models[*].tokens.thoughts` values are summed exactly once into reasoning output;
- `write_file`/`replace` record only non-empty workspace-relative paths with no NUL, absolute root, `~`, or `..` traversal;
- `google_web_search`, `web_fetch`, `browser_*` increment `web_searches`;
- `mcp_*` tool names increment `mcp_calls`;
- `QUALOCK_GEMINI_SHELL_SANDBOX_FAILURE` in a matching shell result appends an evidence error independently of exit marker parsing;
- terminal `status="error"` records typed provider error evidence without inventing usage;
- `message` is known; additive unknown event types are retained.

- [ ] **Step 3: Run RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_gemini_stream_json.py
```

Expected: import failure.

- [ ] **Step 4: Implement strict parser helpers**

Use a `tool_id -> command index` map. Parse marker lines with `^QUALOCK_GEMINI_EXIT_CODE=([0-9]{1,3})$`, then range-check `0..255`. Keep raw transcript unchanged. Normalize only terminal aggregate stats to avoid double-counting model calls. Set `reasoning_output_tokens` to the sum of non-negative integer `stats.models[*].tokens.thoughts` values when that structurally valid detail is present; otherwise use `0`. Never infer thought tokens from prose or `total_tokens`.
- [ ] **Step 5: Run GREEN + static checks**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_gemini_stream_json.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/evidence/gemini_stream_json.py tests/unit/test_gemini_stream_json.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/qualock/evidence/gemini_stream_json.py tests/unit/test_gemini_stream_json.py
git commit -m "feat: parse Gemini stream evidence"
```

### Task 4: Build the Isolated Gemini Invocation Adapter

**Files:**
- Create: `src/qualock/agents/gemini.py`
- Create: `tests/unit/test_gemini_adapter.py`

**Interfaces:**
- Produces `select_gemini_automation_credential(environment: Mapping[str, str]) -> tuple[str, str] | None`.
- Produces `GeminiAdapter(automation_credential: tuple[str, str] | None)` implementing `AgentAdapter`.
- `runtime_dependencies -> ()`; `DockerRunner.prepare` already installs/checks Bubblewrap as a generic prepared-image dependency, so Gemini must not add a second installation path.
- `runtime_overlays` returns exactly one Node overlay from Global Constraints with validation command `("/opt/qualock/node-runtime/bin/node", "--version")`.
- [ ] **Step 1: Write adapter RED tests for credential, argv, environment, and mounts**

Require `select_gemini_automation_credential` to accept only a non-empty `GEMINI_API_KEY`. For an invocation, assert:

```python
assert invocation.container_binary_path == "/opt/qualock/gemini"
assert invocation.stdin_secret_env == ("GEMINI_API_KEY", "test-only")
env = dict(invocation.environment)
assert env["HOME"] == "/opt/qualock/gemini-home"
assert env["GEMINI_CLI_HOME"] == "/opt/qualock/gemini-home"
assert env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] == "/opt/qualock/gemini-settings.json"
assert env["GEMINI_SANDBOX"] == "false"
assert invocation.tmpfs_mounts == ("/opt/qualock/gemini-home",)
```

Assert argv contains `--prompt`, exact prompt, `--output-format stream-json`, `--model`, exact model, `--approval-mode yolo`, and `-e none`; it must not contain the secret, resume/session flags, response recording, include directories, policy files, or extension names. Non-`provider-default` reasoning effort raises before yielding.

Mount assertions: system settings -> `/opt/qualock/gemini-settings.json:ro`; empty directory -> `/workspace/.gemini:ro`; wrapper -> `/opt/qualock/bin/bash:ro`. All temporary source paths disappear when the context manager exits.

- [ ] **Step 2: Pin the enforced settings payload in tests**

```python
settings = {
    "mcpServers": {}, "policyPaths": [], "adminPolicyPaths": [],
    "general": {"enableAutoUpdate": False, "enableAutoUpdateNotification": False,
                "checkpointing": {"enabled": False}},
    "privacy": {"usageStatisticsEnabled": False},
    "advanced": {"ignoreLocalEnv": True},
    "context": {"fileName": [], "includeDirectories": [], "loadMemoryFromIncludeDirectories": False},
    "tools": {"shell": {"enableInteractiveShell": False}, "core": [
        "read_file", "read_many_files", "list_directory", "glob", "grep_search",
        "write_file", "replace", "run_shell_command",
    ]},
    "security": {"auth": {"selectedType": "gemini-api-key", "enforcedType": "gemini-api-key"},
                 "blockedEnvironmentVariables": ["GEMINI_API_KEY"]},
    "skills": {"enabled": False}, "hooksConfig": {"enabled": False}, "hooks": {},
    "telemetry": {"enabled": False, "logPrompts": False},
    "experimental": {"enableAgents": False},
}
```
- [ ] **Step 3: Pin the exact shell wrapper in tests**

```sh
#!/bin/sh
set -eu
unset GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_APPLICATION_CREDENTIALS \
  GOOGLE_CLOUD_PROJECT GOOGLE_CLOUD_PROJECT_ID GOOGLE_CLOUD_LOCATION
if ! /usr/bin/bwrap --unshare-user --unshare-net --bind / / --dev /dev -- /bin/true; then
  echo QUALOCK_GEMINI_SHELL_SANDBOX_FAILURE >&2
  exit 125
fi
set +e
/usr/bin/bwrap --unshare-user --unshare-net --bind / / --dev /dev -- /bin/bash "$@"
qualock_status=$?
printf '\nQUALOCK_GEMINI_EXIT_CODE=%s\n' "$qualock_status" >&2
exit "$qualock_status"
```

Assert both Bubblewrap invocations contain exactly one `--unshare-user`, `--unshare-net`, `--bind / /`, and `--dev /dev`; credentials are unset first; marker is printed exactly once after the real child; there is no `/bin/bash` path outside Bubblewrap.

- [ ] **Step 4: Run adapter RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_gemini_adapter.py
```

Expected: import failure.
- [ ] **Step 5: Implement adapter isolation artifacts and invocation**

Use one `TemporaryDirectory(prefix="qualock-gemini-")` to create the system settings JSON, executable wrapper, and empty `project-gemini/` directory. Set `PATH` to `/opt/qualock/bin:/opt/qualock/node-runtime/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin` so upstream PATH-resolved `bash` is intercepted and the JS shebang finds pinned Node.

Build argv as:

```python
(
    str(binary.path), "--prompt", prompt,
    "--output-format", "stream-json",
    "--model", model,
    "--approval-mode", "yolo",
    "-e", "none",
)
```

Set `stdin_secret_env` only when credential selection succeeded. `parse_evidence` ignores stderr and delegates stdout lines to `parse_gemini_stream_json`.

- [ ] **Step 6: Run GREEN + cross-parser tests**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_gemini_adapter.py tests/unit/test_gemini_stream_json.py
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/agents/gemini.py tests/unit/test_gemini_adapter.py
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add src/qualock/agents/gemini.py tests/unit/test_gemini_adapter.py
git commit -m "feat: add isolated Gemini CLI adapter"
```
### Task 5: Route Gemini Through Current Mainline Local Flows and Pricing

**Files:**
- Modify: `src/qualock/config/models.py`
- Modify: `src/qualock/config/io.py`
- Modify: `src/qualock/commands.py`
- Modify: `src/qualock/cli.py`
- Modify: `src/qualock/pricing/resolve.py`
- Modify: `src/qualock/pricing/sidecar.py`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/test_commands.py`
- Test: `tests/unit/test_cli.py`
- Test: `tests/unit/test_pricing_resolve.py`
- Test: `tests/unit/test_pricing_provenance.py`
- Test: `tests/unit/test_pricing_sidecar_loader.py`
- Test: `tests/unit/test_pricing_analysis.py`
- Test: `tests/unit/test_pricing_render.py`
- Regression: `tests/unit/test_history_render.py`
- Regression: release-monitor/scheduler/version-bisect/GitHub-PR test modules only; no production edits there.

**Interfaces:**
- `AgentConfig.name` accepts `codex|claude|antigravity|gemini`.
- `ModelConfig.reasoning_effort` accepts `low|medium|high|xhigh|provider-default`.
- Produces pure `validate_agent_model_contract(config: QualockConfig) -> None`, called by `load_config` immediately after Pydantic validation.
- `parse_agent_spec`, display-name routing, `_default_resolver`, and `_default_backend` gain Gemini branches.
- Pricing adds Gemini-specific model/trust extraction while mapping `gemini -> google` independently of Antigravity mappings.

- [ ] **Step 1: Write config-contract RED tests**
```python
def test_accepts_gemini_provider_default_contract() -> None:
    config = QualockConfig.model_validate({
        "agent": {"name": "gemini"},
        "model": {"id": "gemini-3.5-flash", "reasoning_effort": "provider-default"},
    })
    validate_agent_model_contract(config)
```

Add negative tests: Gemini + any explicit QuaLock effort fails; non-Gemini + `provider-default` fails; Gemini with untouched default `gpt-5.6-terra` fails as not explicitly configured. `load_config` converts these semantic errors to `ConfigError`. Existing default config remains byte-equivalent Codex + high.

- [ ] **Step 2: Write routing/doctor/credential RED tests**

Require:

- `parse_agent_spec("gemini@0.58.0") == ("gemini", "0.58.0")`;
- `agent_display_name("gemini") == "Gemini CLI"`;
- `_default_resolver("gemini")` returns `GeminiResolver`;
- `_default_backend` rejects missing/empty `GEMINI_API_KEY` even when other Google credentials exist;
- a non-empty API key constructs `GeminiAdapter(("GEMINI_API_KEY", value))` and no other credential;
- `doctor` for Gemini checks Git, npm, Docker, host Node >=20, non-empty Gemini API key, and canaries, without provider/network calls;
- baseline/check pin and compare `gemini` exactly like existing agents, including baseline executable SHA mismatch before candidate execution;
- `start` and `history` regression tests remain unchanged because they are agent-neutral.

- [ ] **Step 3: Write Gemini pricing RED tests**
Use raw stream-json fixtures in pricing tests to prove:

- `provider_for_agent("gemini") == "google"` and Antigravity still maps independently;
- agreeing `init.model` plus terminal `stats.models` selects a pinned canonical Google model with source `runtime_observed`;
- no model observation -> `missing_observed_model`;
- malformed model field -> `malformed_model_evidence`;
- conflicting init/terminal/attempt observations -> `inconsistent_observed_model`;
- observed model absent from current pinned Google catalog -> `unknown_model`, never an alias guess;
- configured exact canonical model disagreeing with runtime observation -> `inconsistent_observed_model`;
- exactly one valid terminal result with non-negative `stats.cached` makes cache-read trust `observed`; duplicate/missing/malformed result makes it `unobserved`;
- Gemini cache-write trust is always `unobserved` in Batch #43;
- sidecar loader accepts `agent="gemini", provider="google"` and still rejects mismatched pairs;
- `qualock cost` remains read-only/offline: a trusted pinned Gemini cohort can price, while unknown/untrusted Gemini history renders normal unavailable guidance and exits zero without writes.

- [ ] **Step 4: Run combined RED**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_config.py tests/unit/test_commands.py tests/unit/test_cli.py tests/unit/test_pricing_resolve.py tests/unit/test_pricing_provenance.py tests/unit/test_pricing_sidecar_loader.py tests/unit/test_pricing_analysis.py tests/unit/test_pricing_render.py
```

Expected: failures because current literals/routing/pricing do not know Gemini.

- [ ] **Step 5: Implement semantic validation and local routing**

```python
def validate_agent_model_contract(config: QualockConfig) -> None:
    effort = config.model.reasoning_effort
    if config.agent.name == "gemini":
        if effort != "provider-default":
            raise ValueError("Gemini requires reasoning_effort: provider-default")
        if config.model.effective_model == "gpt-5.6-terra":
            raise ValueError("Gemini requires an explicit Gemini CLI model")
    elif effort == "provider-default":
        raise ValueError("provider-default reasoning effort is only supported by Gemini")
```
Call this helper immediately after `QualockConfig.model_validate(raw)` in `load_config`; convert its `ValueError` to the existing `ConfigError` surface. Extend command routing only in existing local-agent branch points. Do not edit production modules under release monitor, scheduler, version bisect, or GitHub PR qualification.

Gemini backend branch:

```python
elif agent_name == "gemini":
    credential = select_gemini_automation_credential(os.environ)
    if credential is None:
        raise CommandError("Gemini qualification requires GEMINI_API_KEY")
    adapter = GeminiAdapter(automation_credential=credential)
```

- [ ] **Step 6: Implement Gemini-specific pricing resolution/trust**

Add `"gemini": "google"` to provider and sidecar agent-provider maps. `resolve_model_identity` must branch on `agent == "gemini"` before the generic provider switch so Gemini never enters `_resolve_antigravity`.

Implement `_scan_gemini_observations(result)` over raw JSONL. Collect non-empty string `init.model` values and keys from terminal `result.stats.models`; malformed required containers/fields return `malformed_model_evidence`. All observations across every attempt must agree. `_resolve_gemini` requires runtime observation; it does not trust configured aliases when runtime evidence is absent.

Implement `_gemini_trust(events_jsonl)` requiring exactly one `result`, object `stats`, and non-negative integer `cached`; return `(observed, unobserved)` only in that case, otherwise `(unobserved, unobserved)`.

- [ ] **Step 7: Run GREEN + orchestration regressions**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_config.py tests/unit/test_commands.py tests/unit/test_cli.py tests/unit/test_pricing_resolve.py tests/unit/test_pricing_provenance.py tests/unit/test_pricing_sidecar_loader.py tests/unit/test_history_render.py
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_release_monitor_flow.py tests/unit/test_scheduler_commands.py tests/unit/test_version_bisect_commands.py tests/unit/test_github_pr_commands.py
```

Expected: PASS; second command proves Batch #44 scopes remain unchanged.
- [ ] **Step 8: Run Task 5 static checks and commit**

```bash
/home/pacmap/qualock-easy/.venv/bin/ruff check src/qualock/config/models.py src/qualock/config/io.py src/qualock/commands.py src/qualock/cli.py src/qualock/pricing/resolve.py src/qualock/pricing/sidecar.py
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock
git diff --check
```

Expected: exit 0.

```bash
git add src/qualock/config/models.py src/qualock/config/io.py src/qualock/commands.py src/qualock/cli.py src/qualock/pricing/resolve.py src/qualock/pricing/sidecar.py tests/unit/test_config.py tests/unit/test_commands.py tests/unit/test_cli.py tests/unit/test_pricing_resolve.py tests/unit/test_pricing_provenance.py tests/unit/test_pricing_sidecar_loader.py tests/unit/test_pricing_analysis.py tests/unit/test_pricing_render.py
git commit -m "feat: route Gemini local qualification"
```

### Task 6: Prove the Mandatory No-Auth Runtime Contract

**Files:**
- Create: `tests/integration/test_gemini_noauth_contract.py`
- No production changes unless the no-auth proof exposes a real contract defect; any such defect starts a fresh scoped fix/re-review loop in the owning Task 1–5 area.

**Interfaces:**
- Local opt-in gate: `QUALOCK_RUN_GEMINI_NOAUTH_CONTRACT=1`.
- Exact package contract: Gemini CLI `0.58.0` already present in QuaLock's resolver cache or supplied as a pre-existing extracted package root; the test must not install dependencies.
- No `GEMINI_API_KEY` is read or required.
- [ ] **Step 1: Add an explicit prerequisite check before writing/running the integration proof**

Controller checks for exact `0.58.0` in the resolver cache. Current-machine reconnaissance found no WSL Gemini resolver cache and the unrelated Windows global Gemini is `0.54.4`; therefore do not silently reuse it. If exact `0.58.0` is absent, record the gate as BLOCKED and stop before any `npm install`. A registry/package fetch requires separate user authorization under the no-install constraint.

- [ ] **Step 2: Write the no-auth Docker contract test**

Use the existing pinned Bookworm-compatible test image:

```text
ghcr.io/astral-sh/uv:0.9.30-python3.12-bookworm@sha256:85d4cb1afa769a7338e095b927bee941cf5ec92266c7424b3f6c0f2748567248
```

Seed a temporary repository with hostile `GEMINI.md`, `.gemini/settings.json`, and `.gemini/.env` sentinel values. Build a `CanarySpec` with `execution="container"`, instantiate the real `GeminiAdapter(None)`, and prepare via `DockerQualificationBackend` so the actual Node overlay/runtime dependency path is exercised.

Resolve `0.58.0` from the pre-existing cache with an intentionally nonexistent npm executable; this proves the mandatory contract uses no registry. Assert the resolved path/version/help/bin/source contract before Docker execution.

- [ ] **Step 3: Prove Node and project-state isolation without provider access**

Inside the prepared image, run `/opt/qualock/node-runtime/bin/node --version` and require major 22. Mount the adapter's empty project-Gemini directory at `/workspace/.gemini` and assert the hostile repository settings/env files are not visible during the agent phase while the source tree itself remains unchanged on disk.

Run the cached Gemini executable only with `--version`/`--help`; never pass `--prompt` and never set a provider credential in this no-auth test.
- [ ] **Step 4: Prove the Bubblewrap network/exit contract**

Using the exact wrapper file produced by `GeminiAdapter.invocation` and the same prepared image/security mode:

1. `/opt/qualock/bin/bash -c 'cat /dev/null; printf dev-ok'` exits 0, prints `dev-ok`, and emits one `QUALOCK_GEMINI_EXIT_CODE=0` marker.
2. `/opt/qualock/bin/bash -c 'exit 7'` exits 7 and emits exactly one `QUALOCK_GEMINI_EXIT_CODE=7` marker.
3. A parent Python direct-IP TCP probe to `1.1.1.1:443` succeeds.
4. The same direct-IP probe through the wrapper fails with network-unreachable/no-route semantics and a non-zero marker.
5. No `QUALOCK_GEMINI_SHELL_SANDBOX_FAILURE` appears during normal namespace operation.

Inspect the Docker create argv and assert it contains no `--privileged`, `--cap-add`, `--network host`, Docker socket mount, or host-device passthrough; the only security option remains the pre-existing `seccomp=unconfined`.

- [ ] **Step 5: Run the opt-in no-auth gate**

```bash
QUALOCK_RUN_GEMINI_NOAUTH_CONTRACT=1 /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/integration/test_gemini_noauth_contract.py
```

Expected: PASS only when exact cached 0.58.0 prerequisite exists. If prerequisite is absent, do not turn the test into a fake pass; record the controller gate as blocked pending explicit artifact-fetch authorization.

- [ ] **Step 6: Run focused regression tests and commit the test harness**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_gemini_resolver.py tests/unit/test_gemini_adapter.py tests/unit/test_gemini_stream_json.py
```
Expected: PASS.

```bash
git add tests/integration/test_gemini_noauth_contract.py tests/unit/test_gemini_resolver.py
git commit -m "test: lock Gemini no-auth runtime contract"
```

### Task 7: Gate the Exact Implementation Head, Review, CI, Document, and Merge

**Files:**
- Modify after implementation-head CI only: `README.md`
- Modify after implementation-head CI only: `ROADMAP.md`
- Modify after implementation-head CI only: `docs/superpowers/specs/2026-09-08-gemini-cli-adapter-design.md`
- SDD ledger/reports under `.superpowers/sdd/2026-09-08-gemini-cli-mainline-adapter/` are controller artifacts and remain untracked if repo convention excludes them.

**Interfaces:**
- Freezes one exact implementation SHA before push.
- Requires one fresh whole-implementation Sonnet-high review with no Critical/Important findings before any push/PR.
- Post-CI docs may move `Additional coding-agent adapters` to Delivered only after exact implementation-head CI is green.
- Final docs-inclusive whole-branch review uses Opus-high exactly once, then merge requires explicit user authorization.

- [ ] **Step 1: Freeze exact implementation head and ancestry**

```bash
BASE=a98374d5c161ef96272e0a94a5f7584511e7f7f9
HEAD=$(git rev-parse HEAD)
git merge-base --is-ancestor "$BASE" "$HEAD"
test -z "$(git status --porcelain)"
printf '%s\n' "$HEAD" > /tmp/b43-implementation-head.txt
```

Expected: clean worktree and successful ancestry.
- [ ] **Step 2: Run fresh implementation-head local gates**

```bash
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q tests/unit/test_gemini_resolver.py tests/unit/test_gemini_stream_json.py tests/unit/test_gemini_adapter.py tests/unit/test_config.py tests/unit/test_commands.py tests/unit/test_cli.py tests/unit/test_pricing_resolve.py tests/unit/test_pricing_provenance.py tests/unit/test_pricing_sidecar_loader.py tests/unit/test_history_render.py
/home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src/qualock
git diff --check "$BASE"..HEAD
```

Expected: focused and full pytest exit 0, compileall/diff-check exit 0.

Run strict mypy:

```bash
set +e
/home/pacmap/qualock-easy/.venv/bin/mypy --strict src/qualock 2>&1 | tee /tmp/b43-mypy.txt
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 1
```

Expected diagnostics are exactly the three current baseline PyYAML `[import-untyped]` errors at `src/qualock/config/io.py:3`, `src/qualock/canary/loader.py:4`, and `src/qualock/project_setup/config.py:6`; no Gemini/overlay errors are allowed.

- [ ] **Step 3: Run Ruff/no-new-debt/protected-scope gates**

Run Ruff on every Python path changed from `$BASE`. Compare full-tree Ruff JSON at `$BASE` versus HEAD and require no new diagnostic on an unchanged Python file. Also require no diff under `src/qualock/release_monitor/`, `src/qualock/scheduler/`, `src/qualock/version_bisect/`, `src/qualock/github_pr/`, `src/qualock/qualification/policy.py`, `src/qualock/source/`, `.github/`, or `pyproject.toml`.
- [ ] **Step 4: Run one fresh whole-implementation pre-push review**

Dispatch one read-only Sonnet-high reviewer on exact range:

```text
a98374d5c161ef96272e0a94a5f7584511e7f7f9..$(cat /tmp/b43-implementation-head.txt)
```

Reviewer must read the canonical spec and audit all 24 Required TDD coverage items, no-auth gate evidence, credential/config isolation, shell network/exit proof, pricing/advisory isolation, existing-agent regressions, and protected scopes. Any Critical/Important finding starts a fresh scoped fixer + exact-head re-review before proceeding.

- [ ] **Step 5: STOP before push/PR unless user explicitly authorizes shared effects**

When authorized, push the exact implementation head and create a non-draft PR. Immediately verify local HEAD = remote branch SHA = PR `headRefOid`; if not identical, stop.

- [ ] **Step 6: Require implementation-head CI green before docs mutation**

Wait for/check every required Linux Python and Windows CI job on the exact PR head. Do not mark docs Delivered while any required job is pending/failing or while PR head differs from `/tmp/b43-implementation-head.txt`.

- [ ] **Step 7: Write only the post-CI delivery docs**

README must document:

- local `gemini@<version>` baseline/check usage;
- `agent.name: gemini`, explicit Gemini model, `reasoning_effort: provider-default`;
- `GEMINI_API_KEY` automation-only credential contract;
- Node overlay/project/user config isolation and deny-network tool-shell boundary;
- pricing is API-equivalent advisory reference only and can be unavailable;
- release monitor/scheduler/bisect/GitHub-PR Gemini parity is deferred to Batch #44.

ROADMAP moves `Additional coding-agent adapters` out of Next and into Delivered with Gemini CLI as the completion evidence. Canonical spec status changes to `Delivered`; do not alter implementation requirements/history.
- [ ] **Step 8: Commit docs and rerun docs-inclusive gates**

```bash
git add README.md ROADMAP.md docs/superpowers/specs/2026-09-08-gemini-cli-adapter-design.md
git diff --cached --check
git diff --cached --name-only
git commit -m "docs: document Gemini CLI qualification"
```

Cached scope must be exactly those three files. Rerun focused tests, full pytest, compileall, strict-mypy baseline check, changed-file Ruff/no-new-debt, diff-check, and protected-scope proof on the docs-inclusive head.

- [ ] **Step 9: STOP before pushing docs-inclusive head unless user authorization covers that push**

After authorization, push the docs commit and require CI green again on the exact docs-inclusive `headRefOid`.

- [ ] **Step 10: Run exactly one final Opus-high whole-branch review**

Review exact base-to-final range, including README/ROADMAP/spec truthfulness. Require:

```text
VERDICT: APPROVED
Critical: 0
Important: 0
DOCS: APPROVED
```

The final reviewer must explicitly triage every deferred Minor and confirm no release/tag/publish/dependency/protected-scope change. Do not invoke a second final Opus review; if it finds blockers, fix them with fresh scoped Sonnet workers/reviewers and use controller evidence for the corrected head.

- [ ] **Step 11: Verify final identity and STOP before merge**

Require local HEAD = remote branch SHA = PR `headRefOid`, PR OPEN/non-draft/MERGEABLE/CLEAN, and every required CI check green. Merge only after explicit user authorization; repository policy uses rebase merge. After merge, fetch `origin/main`, verify PR state `MERGED`, and require tree diff from final PR head to new main to be empty.

No tag, GitHub Release, PyPI publish, or hosted/commercial action belongs to Batch #43.

## Spec Coverage Map

| Spec TDD obligation | Owning task |
| --- | --- |
| 1 config/effort fail-before-resolution | Task 5 |
| 2 agent spec/display routing | Task 5 |
| 3 exact/latest/npm/cache behavior | Task 2 |
| 4 executable digest/tamper/symlink/no-registry reuse | Task 2 |
| 5 bin entrypoint/version/help flags | Task 2 |
| 6 PATH-resolved bash interception contract | Task 2 |
| 7 digest-pinned runtime overlay | Task 1 |
| 8 no-overlay legacy preparation equivalence | Task 1 |
| 9 Gemini argv/private home/extensions | Task 4 |
| 10 hostile user/project config isolation | Tasks 4 and 6 |
| 11 system settings hardening | Task 4 |
| 12 API-key non-leakage | Tasks 4 and 6 |
| 13 Bubblewrap wrapper structure | Task 4 |
| 14 no-auth Docker network/dev/exit proof | Task 6 |
| 15 stream/model/tool/usage parser | Task 3 |
| 16 exact shell marker semantics | Task 3 |
| 17 baseline digest/mixed-agent routing | Task 5 |
| 18 offline actionable doctor | Task 5 |
| 19 unchanged history output/dependency direction | Task 5 |
| 20 Gemini-vs-Antigravity pricing identity | Task 5 |
| 21 conservative Gemini usage trust | Task 5 |
| 22 read-only/offline Gemini cost behavior | Task 5 |
| 23 Batch #44 scopes remain unsupported | Task 5 |
| 24 existing-agent/history/budget/pricing regression suite | Tasks 5 and 7 |
