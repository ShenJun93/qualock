# Linux Host-Agent Runner and Antigravity Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Linux/WSL host qualification backend for locally pinned Antigravity CLI versions without weakening the existing Docker-backed Codex/Claude path.

**Architecture:** `QualificationExecutor` remains backend-neutral. Container canaries continue through `DockerQualificationBackend`; explicit `linux-host` canaries use `LinuxHostQualificationBackend`, which runs Antigravity inside an outer Bubblewrap namespace plus Antigravity's native terminal sandbox and a QuaLock `PreToolUse` security hook.

**Tech Stack:** Python 3.12+, Pydantic, pytest, Git CLI, Bubblewrap, Antigravity CLI `agy`, existing QuaLock process/evidence/qualification modules.

**Spec:** `docs/superpowers/specs/2026-09-05-linux-host-agent-runner-antigravity-design.md` at or after commit `b1f4d78`.

## Global Constraints

- Linux/WSL only; never invoke Windows `agy.exe` for qualification.
- Existing Codex and Claude Code behavior stays Docker-backed and unchanged.
- `RuntimeSpec.execution` defaults to `container`; Antigravity host runs require explicit `linux-host`.
- No automatic Antigravity download endpoint: resolve only an explicitly supplied or PATH-installed native Linux binary and verify exact version plus SHA-256.
- Preserve normal WSL account auth; never copy, parse, serialize, fingerprint, log, or rewrite `antigravity-oauth-token`.
- The token may only be read-only bind-mounted by Bubblewrap into a fresh private app-data tree.
- Every attempt gets fresh private config, app-data, project state, and outer `/tmp`; never reuse an Antigravity profile between attempts.
- Always use `--new-project --sandbox --disable-slash-commands --output-format stream-json` and `AGY_CLI_DISABLE_AUTO_UPDATE=true`.
- Never use `--dangerously-skip-permissions`, Windows fallback, API-key substitution, host networking flags, Docker socket mounts, or unsandboxed retry.
- The QuaLock hook treats `QUALOCK_WORKSPACE=/tmp/qualock-workspace` as authority; runtime `workspacePaths` is corroborating evidence only.
- Hook policy is default-deny: allow only the exact file/shell tools supported by this batch, validate file targets with `realpath`, and hard-deny web/browser/MCP/subagent/permission-escalation surfaces.
- No push, PR, merge, tag, release, or publication in implementation tasks.

---

## File Structure
- Modify `src/qualock/canary/models.py`: add explicit container vs Linux-host runtime profile validation.
- Modify `src/qualock/run/models.py`: replace Docker-named `PreparedImage` with backend-neutral `PreparedTarget`; add host attempt state if needed.
- Modify `src/qualock/run/executor.py`, `src/qualock/run/backend.py`, `src/qualock/run/docker.py`: mechanical `PreparedTarget` migration and container-profile rejection.
- Create `src/qualock/agents/antigravity_resolver.py`: local native-Linux binary resolution, exact version validation, capability validation, SHA-256 pinning.
- Create `src/qualock/evidence/antigravity_stream_json.py`: fail-closed parser for Antigravity NDJSON.
- Create `src/qualock/agents/antigravity.py`: generated private profile, security hook, Antigravity invocation contract.
- Create `src/qualock/run/host.py`: Bubblewrap command construction, process-tree execution, Git state inspection, host setup/grading helpers.
- Create `src/qualock/run/host_backend.py`: `LinuxHostQualificationBackend` orchestration and integrity policy enforcement.
- Modify `src/qualock/config/models.py` and `src/qualock/commands.py`: expose `antigravity@<version>` and route it to the host backend.
- Add focused unit/integration tests under `tests/unit/` and `tests/integration/`; add only sanitized fixtures if parser tests need stable real-contract samples.

### Task 1: Make prepared targets and canary runtime profiles backend-neutral

**Files:**
- Modify: `src/qualock/canary/models.py`
- Modify: `src/qualock/run/models.py`
- Modify: `src/qualock/run/executor.py`
- Modify: `src/qualock/run/backend.py`
- Modify: `src/qualock/run/docker.py`
- Modify tests importing `PreparedImage`, especially `tests/integration/test_fake_qualification.py`, `tests/unit/test_budgeted_qualification.py`, `tests/unit/test_docker_backend.py`, and `tests/unit/test_docker_commands.py`

**Interfaces:**
- Produces: `RuntimeSpec.execution: Literal["container", "linux-host"]`
- Produces: `PreparedTarget(reference: str, digest: str)`
- Preserves: `QualificationBackend.prepare(...) -> PreparedTarget`

- [ ] **Step 1: Write failing runtime-profile and prepared-target tests**
```python
from pydantic import ValidationError
from qualock.canary.models import RuntimeSpec
from qualock.run.models import PreparedTarget


def test_runtime_defaults_to_container() -> None:
    runtime = RuntimeSpec(image="python:3.12")
    assert runtime.execution == "container"
    assert runtime.image == "python:3.12"


def test_linux_host_runtime_rejects_container_image() -> None:
    with pytest.raises(ValidationError):
        RuntimeSpec(execution="linux-host", image="python:3.12")


def test_linux_host_runtime_allows_no_image() -> None:
    assert RuntimeSpec(execution="linux-host").image is None


def test_prepared_target_is_backend_neutral() -> None:
    assert PreparedTarget("ref", "sha256:x").digest == "sha256:x"
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_canary_models.py tests/integration/test_fake_qualification.py tests/unit/test_docker_backend.py -q`
Expected: FAIL because `RuntimeSpec.execution` and `PreparedTarget` do not exist.

- [ ] **Step 3: Implement the minimal schema/type migration**
```python
class RuntimeSpec(BaseModel):
    execution: Literal["container", "linux-host"] = "container"
    image: str | None = None

    @model_validator(mode="after")
    def validate_runtime(self) -> "RuntimeSpec":
        if self.execution == "container" and not self.image:
            raise ValueError("container runtime requires image")
        if self.execution == "linux-host" and self.image is not None:
            raise ValueError("linux-host runtime must not set image")
        return self


@dataclass(frozen=True)
class PreparedTarget:
    reference: str
    digest: str
```

Update Docker preparation to reject `canary.runtime.execution != "container"` before reading `image`; rename `PreparedImage` imports/usages mechanically without changing Docker behavior.

- [ ] **Step 4: Run focused and regression tests**

Run: `PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/integration/test_fake_qualification.py tests/unit/test_budgeted_qualification.py tests/unit/test_docker_backend.py tests/unit/test_docker_commands.py tests/unit/test_canary_models.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/qualock/canary/models.py src/qualock/run tests
git commit -m "refactor: add backend-neutral prepared targets"
```

### Task 2: Add a local-only Antigravity resolver

**Files:**
- Create: `src/qualock/agents/antigravity_resolver.py`
- Create: `tests/unit/test_antigravity_resolver.py`

**Interfaces:**
- Produces: `AntigravityResolver(binary_path: Path | None = None)`
- Produces: `AntigravityResolveError`
- Produces: `resolve(version: str) -> AgentBinary`

- [ ] **Step 1: Write resolver RED tests**
```python
def test_resolve_pins_exact_native_linux_binary(tmp_path: Path, monkeypatch) -> None:
    agy = write_fake_agy(tmp_path, version="1.1.27", help_text="--sandbox --output-format --model --effort --new-project --disable-slash-commands")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    binary = AntigravityResolver(agy).resolve("1.1.27")
    assert binary.name == "antigravity"
    assert binary.version == "1.1.27"
    assert binary.path == agy.resolve()
    assert len(binary.sha256) == 64


def test_resolve_rejects_version_mismatch(tmp_path: Path, monkeypatch) -> None:
    agy = write_fake_agy(tmp_path, version="1.1.27")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    with pytest.raises(AntigravityResolveError, match="requested 1.1.26"):
        AntigravityResolver(agy).resolve("1.1.26")


def test_resolve_rejects_windows_executable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    with pytest.raises(AntigravityResolveError, match="native Linux"):
        AntigravityResolver(tmp_path / "agy.exe").resolve("1.1.27")
```

Also cover missing executable, non-Linux host, non-zero `--version`, and missing required help flags.

- [ ] **Step 2: Run resolver tests and verify RED**

Run: `PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_antigravity_resolver.py -q`
Expected: FAIL because resolver module does not exist.

- [ ] **Step 3: Implement local resolution only**

Use `shutil.which("agy")` only when `binary_path` is absent; require `platform.system() == "Linux"`, executable non-`.exe` path, exact `agy --version`, required headless/sandbox flags in `agy --help`, then hash the file with SHA-256. Do not add any download or latest-version lookup.

- [ ] **Step 4: Run tests and commit**

Run: `PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_antigravity_resolver.py -q`
Expected: PASS.

```bash
git add src/qualock/agents/antigravity_resolver.py tests/unit/test_antigravity_resolver.py
git commit -m "feat: resolve local Antigravity binaries"
```

### Task 3: Parse Antigravity stream-json fail-closed

**Files:**
- Create: `src/qualock/evidence/antigravity_stream_json.py`
- Create: `tests/unit/test_antigravity_stream_json.py`

**Interfaces:**
- Produces: `AntigravityEvidenceError(AgentEvidenceError)`
- Produces: `parse_antigravity_stream_json(lines: Iterable[str]) -> AgentEvidence`

- [ ] **Step 1: Write parser RED tests from observed 1.1.27 event shapes**
```python
def test_parses_success_usage_and_tool_invocations() -> None:
    evidence = parse_antigravity_stream_json([
        '{"event":"init","conversation_id":"c1","init":{"permission_mode":"proceed-in-sandbox"}}',
        '{"event":"step_update","step_update":{"step_index":2,"state":"ACTIVE","step_type":"tool","tool_name":"run_command","tool_info":{"name":"run_command","parameters":{"CommandLine":"python3 probe.py"}}}}',
        '{"event":"step_update","step_update":{"step_index":2,"state":"DONE","step_type":"tool","tool_name":"run_command","tool_info":{"name":"run_command","parameters":{"CommandLine":"python3 probe.py"},"output":"ok\\r\\n"}}}',
        '{"event":"result","result":{"conversation_id":"c1","status":"SUCCESS","usage":{"input_tokens":10,"output_tokens":2,"thinking_tokens":1,"cache_read_tokens":3,"total_tokens":12}}}',
    ])
    assert evidence.thread_id == "c1"
    assert evidence.commands == [CommandEvent(command="python3 probe.py", exit_code=None)]
    assert evidence.input_tokens == 10
    assert evidence.cached_input_tokens == 3
    assert evidence.reasoning_output_tokens == 1


def test_does_not_infer_shell_exit_code_from_done_state() -> None:
    # Real 1.1.27 can emit tool state DONE for a shell process that exited 2.
    evidence = parse_antigravity_stream_json(failed_shell_done_fixture())
    assert evidence.commands[0].exit_code is None


def test_counts_forbidden_tool_attempt_once() -> None:
    evidence = parse_antigravity_stream_json(denied_search_web_fixture())
    assert evidence.web_searches == 1


def test_missing_or_duplicate_terminal_result_fails_closed() -> None:
    with pytest.raises(AntigravityEvidenceError):
        parse_antigravity_stream_json([])
```

Also cover malformed JSON, duplicate init/result, terminal `ERROR`, non-empty `denied_actions`, MCP/subagent/browser families, and successful file-change paths.

- [ ] **Step 2: Run parser tests and verify RED**

Run: `PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_antigravity_stream_json.py -q`
Expected: FAIL because parser module does not exist.

- [ ] **Step 3: Implement parser using `step_index` to de-duplicate ACTIVE/DONE pairs**

Record tool invocation on first `ACTIVE` event per `step_index`; use later DONE/ERROR only for outcome metadata. Never infer shell exit code from tool state or assistant prose. Require exactly one init and terminal result; require init permission mode `proceed-in-sandbox`; map result usage fields exactly.

- [ ] **Step 4: Run tests and commit**

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_antigravity_stream_json.py -q
git add src/qualock/evidence/antigravity_stream_json.py tests/unit/test_antigravity_stream_json.py
git commit -m "feat: parse Antigravity stream evidence"
```

### Task 4: Generate a process-private Antigravity security profile

**Files:**
- Create: `src/qualock/agents/antigravity.py`
- Create: `tests/unit/test_antigravity_adapter.py`

**Interfaces:**
- Produces: `AntigravityInvocation(argv, environment, config_root, app_data_root, oauth_token_path, workspace_mount)`
- Produces: `AntigravityAdapter(auth_app_data: Path)` with `invocation(..., workspace: Path, timeout_seconds: int)` context manager
- Consumes: `parse_antigravity_stream_json`

- [ ] **Step 1: Write RED tests for generated settings, plugin and hook**
```python
def test_invocation_builds_fresh_private_profile(tmp_path: Path) -> None:
    token = tmp_path / "real-app" / "antigravity-oauth-token"
    token.parent.mkdir()
    token.write_text("DO_NOT_COPY")
    adapter = AntigravityAdapter(auth_app_data=token.parent)
    binary = AgentBinary("antigravity", "1.1.27", tmp_path / "agy", "a" * 64)

    with adapter.invocation(
        binary,
        model="gemini-3.8-flash-medium",
        reasoning_effort="medium",
        prompt="edit target",
        workspace=tmp_path / "workspace",
        timeout_seconds=60,
    ) as invocation:
        settings = json.loads((invocation.app_data_root / "settings.json").read_text())
        assert settings["enableTerminalSandbox"] is True
        assert settings["toolPermission"] == "proceed-in-sandbox"
        assert "DO_NOT_COPY" not in json.dumps(settings)
        assert invocation.oauth_token_path == token
        assert invocation.workspace_mount == "/tmp/qualock-workspace"
```

Add hook unit tests that feed JSON on stdin and assert: `view_file`/`write_to_file` inside `QUALOCK_WORKSPACE` return allow; outside paths return `{"decision":"deny"}`; `search_web`, browser, MCP, subagent, permission-escalation and unknown tools are denied; symlink escape resolves outside and is denied; malformed hook input fails closed.

- [ ] **Step 2: Run adapter tests and verify RED**

Run: `PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_antigravity_adapter.py -q`
Expected: FAIL because adapter module does not exist.

- [ ] **Step 3: Implement fresh profile generation and invocation**

Generate per-context temporary roots containing:

```text
config/import_manifest.json
config/plugins/qualock-security/plugin.json
config/plugins/qualock-security/hooks.json
config/plugins/qualock-security/qualock_gate.py
appdata/settings.json
appdata/antigravity-oauth-token   # empty mountpoint only; never populated by Python
```

Build Antigravity argv with `--new-project -p <prompt> --model <model> --effort <low|medium|high> --sandbox --disable-slash-commands --output-format stream-json --print-timeout <timeout>s`. Reject `xhigh` explicitly. Environment must include `AGY_CLI_DISABLE_AUTO_UPDATE=true` and `QUALOCK_WORKSPACE=/tmp/qualock-workspace`.

- [ ] **Step 4: Verify generated profile contains no credential bytes**

Test by placing a unique sentinel in the real token file and recursively reading only the generated temporary profile; assert the sentinel is absent. Do not read the real token in production code.

- [ ] **Step 5: Run tests and commit**

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_antigravity_adapter.py tests/unit/test_antigravity_stream_json.py -q
git add src/qualock/agents/antigravity.py tests/unit/test_antigravity_adapter.py
git commit -m "feat: generate Antigravity security profile"
```

### Task 5: Add the Linux host runner and two-layer isolation

**Files:**
- Create: `src/qualock/run/host.py`
- Modify: `src/qualock/run/process.py`
- Create: `tests/unit/test_host_runner.py`
- Modify: `tests/unit/test_process.py` if process-tree tests live there

**Interfaces:**
- Produces: `HostAgentState(workspace, stdout, stderr, exit_code, elapsed_ms)`
- Produces: `LinuxHostRunner.run_agent(workspace, invocation, timeout_seconds) -> HostAgentState`
- Produces: `LinuxHostRunner.inspect_agent_state(workspace) -> AgentStateEvidence`
- Produces: `LinuxHostRunner.run_setup(...)` and `run_grader(...)`

- [ ] **Step 1: Write RED tests for exact Bubblewrap argv and process-tree timeout**
```python
def test_build_agent_argv_has_private_tmp_and_read_only_token(tmp_path: Path) -> None:
    argv = LinuxHostRunner().build_agent_argv(
        workspace=tmp_path / "attempt",
        invocation=fake_invocation(tmp_path),
    )
    assert argv[:4] == ["bwrap", "--unshare-user", "--bind", "/"]
    assert ["--tmpfs", "/tmp"] == argv[argv.index("--tmpfs"):argv.index("--tmpfs") + 2]
    assert "/tmp/qualock-workspace" in argv
    token_index = argv.index("--ro-bind")
    assert "antigravity-oauth-token" in argv[token_index + 1]
    assert "--share-net" not in argv
    assert "--unshare-net" not in argv  # parent needs provider network; inner sandbox blocks tool-shell network
    assert "--dangerously-skip-permissions" not in argv
```

Add a timeout test that starts a child process which itself starts a long-lived child; after timeout assert the process group is gone. Implement a dedicated `run_process_tree(...)` using `start_new_session=True`, `os.killpg(..., SIGKILL)` on timeout; do not change existing `run_process` semantics globally.

- [ ] **Step 2: Run host-runner tests and verify RED**

Run: `PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_host_runner.py tests/unit/test_process.py -q`
Expected: FAIL because host runner/process-group helper do not exist.

- [ ] **Step 3: Implement the outer namespace exactly as accepted**

Bubblewrap order must create a private `/tmp`, create `/tmp/qualock-workspace`, bind the host attempt workspace there RW, bind the fresh private config/app-data trees over the normal Antigravity paths, and read-only bind the existing OAuth token into the private app-data token mountpoint. Add `--dev /dev` so PTY creation works. Run the Antigravity argv with cwd `/tmp/qualock-workspace`.

- [ ] **Step 4: Implement host setup, Git inspection and grader helpers**

`run_setup` executes each `canary.setup` command with `sh -lc` in the prepared host workspace and fails on timeout/non-zero. `inspect_agent_state` uses QuaLock-owned Git commands (`git diff --name-only -z HEAD`, `git ls-files --others --exclude-standard -z`, `git diff --binary HEAD`). `run_grader` copies the attempt workspace to a grader-only directory, applies the hidden patch, then runs grader commands with explicit timeout and a scrubbed environment.

- [ ] **Step 5: Run unit tests and commit**

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_host_runner.py tests/unit/test_process.py -q
git add src/qualock/run/host.py src/qualock/run/process.py tests/unit/test_host_runner.py tests/unit/test_process.py
git commit -m "feat: add isolated Linux host runner"
```

### Task 6: Implement `LinuxHostQualificationBackend`

**Files:**
- Create: `src/qualock/run/host_backend.py`
- Create: `tests/unit/test_host_backend.py`

**Interfaces:**
- Produces: `LinuxHostQualificationBackend.prepare(canary, qualification_id) -> PreparedTarget`
- Produces: `LinuxHostQualificationBackend.run_attempt(...) -> AttemptResult`
- Consumes: `GitSourceManager`, `LinuxHostRunner`, `AntigravityAdapter`, existing `IntegrityPolicy`

- [ ] **Step 1: Write RED tests for preparation and attempt isolation**

Test that `prepare` rejects `runtime.execution != "linux-host"`, materializes the base SHA, runs setup, and returns a deterministic digest from repository URL/base SHA/setup inputs. Test that two repetitions copy from the same prepared template into distinct attempt directories and never reuse mutated agent state.

- [ ] **Step 2: Write RED tests for validity/integrity decisions**

Use fake runner/adapter evidence to assert invalid attempts for agent timeout/non-zero exit, terminal evidence error, web/MCP counters, protected path modifications, and parser failure. Assert a successful attempt grades only after evidence and integrity checks pass.

- [ ] **Step 3: Run backend tests and verify RED**

Run: `PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_host_backend.py -q`
Expected: FAIL because backend does not exist.
- [ ] **Step 4: Implement backend orchestration**

Preparation path:

```python
source_dir = work_root / qualification_id / canary.id / "prepared"
source_manager.materialize(url, base_sha, source_dir)
host_runner.run_setup(source_dir, canary.setup, timeout_seconds=canary.agent.timeout_seconds)
return PreparedTarget(reference=str(source_dir), digest=prepared_digest(...))
```

Attempt path: copy the prepared template to a unique attempt directory; enter `AntigravityAdapter.invocation(...)`; call `host_runner.run_agent`; parse evidence; inspect Git state; apply `protected_path_violations`; enforce the same `IntegrityPolicy` semantics as Docker; only then run the hidden grader copy. Cleanup attempt/profile directories in `finally` paths.

- [ ] **Step 5: Run backend plus executor integration tests**

Run: `PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_host_backend.py tests/integration/test_fake_qualification.py tests/unit/test_budgeted_qualification.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/qualock/run/host_backend.py tests/unit/test_host_backend.py
git commit -m "feat: add Linux host qualification backend"
```

### Task 7: Route Antigravity through public baseline/check commands

**Files:**
- Modify: `src/qualock/config/models.py`
- Modify: `src/qualock/commands.py`
- Modify: `src/qualock/cli.py` only where display/help text enumerates agents
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_cli.py`
- Create or modify: `tests/unit/test_commands.py` for resolver/backend selection

**Interfaces:**
- `AgentConfig.name` accepts `"antigravity"`.
- `parse_agent_spec()` accepts `antigravity@<version>`.
- `agent_display_name("antigravity") == "Antigravity"`.
- `_default_resolver("antigravity")` uses `QUALOCK_ANTIGRAVITY_BIN` when set, otherwise PATH `agy`.
- `_default_backend(..., "antigravity")` returns `LinuxHostQualificationBackend`.

- [ ] **Step 1: Write routing RED tests**

```python
def test_parse_agent_spec_accepts_antigravity() -> None:
    assert parse_agent_spec("antigravity@1.1.27") == ("antigravity", "1.1.27")


def test_config_accepts_antigravity_agent() -> None:
    config = QualockConfig.model_validate({"agent": {"name": "antigravity"}})
    assert config.agent.name == "antigravity"


def test_antigravity_display_name() -> None:
    assert agent_display_name("antigravity") == "Antigravity"
```

Also assert Codex/Claude routing remains unchanged and no version-bisect/release-monitor/GitHub-PR command is widened to Antigravity in this batch.

- [ ] **Step 2: Run routing tests and verify RED**

Run: `PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_config.py tests/unit/test_cli.py tests/unit/test_commands.py -q`
Expected: FAIL on unsupported `antigravity`.

- [ ] **Step 3: Implement routing and explicit local-binary override**

For Antigravity only, read `QUALOCK_ANTIGRAVITY_BIN` as a path override; do not use it as a credential. Construct `AntigravityAdapter(auth_app_data=Path.home()/".gemini/antigravity-cli")` and `LinuxHostRunner()`. Preserve Docker backend construction byte-for-byte for Codex/Claude except imports/type migration already covered by Task 1.

- [ ] **Step 4: Run routing/regression tests and commit**

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_config.py tests/unit/test_cli.py tests/unit/test_commands.py -q
git add src/qualock/config/models.py src/qualock/commands.py src/qualock/cli.py tests/unit
git commit -m "feat: route Antigravity host qualification"
```
### Task 8: Lock the real `agy 1.1.27` contract and finish Batch #35 gates

**Files:**
- Create: `tests/integration/test_antigravity_real_contract.py`
- Optionally create sanitized fixtures under `tests/fixtures/antigravity/` only after asserting no account/token/home-path secret material is present.
- Modify: `README.md` or the existing agent-support documentation only where local Antigravity setup is documented.

**Interfaces:**
- Real test is opt-in via `QUALOCK_RUN_ANTIGRAVITY_REAL=1`.
- Real binary path comes from `QUALOCK_ANTIGRAVITY_BIN` or PATH and must resolve as `1.1.27` for the certified fixture/probe.
- Real model pin for the acceptance test is `gemini-3.8-flash-medium`; production adapter remains model-configurable.

- [ ] **Step 1: Write the opt-in real-contract test**

The test creates only throwaway paths and proves in one authenticated run:

```text
init.permission_mode == proceed-in-sandbox
inside workspace view/write succeeds
outside workspace file tool is denied by PreToolUse hook
search_web is denied
invoke_subagent is denied
run_command cwd == /tmp/qualock-workspace
HOME sentinel is invisible
host /tmp sentinel is invisible because outer /tmp is private
outbound direct TCP from tool shell is blocked
terminal result == SUCCESS
user real import_manifest/settings/token remain unchanged
```

Do not assert or print token contents. Hashing the token is also forbidden; compare only file metadata that does not expose content when necessary, or preferably assert the test never writes those paths.

- [ ] **Step 2: Run no-auth/unit suite first**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest tests/unit/test_antigravity_resolver.py tests/unit/test_antigravity_stream_json.py tests/unit/test_antigravity_adapter.py tests/unit/test_host_runner.py tests/unit/test_host_backend.py -q
```

Expected: PASS without requiring Antigravity authentication.

- [ ] **Step 3: Run one authenticated real contract**

Run:

```bash
QUALOCK_RUN_ANTIGRAVITY_REAL=1 \
QUALOCK_ANTIGRAVITY_BIN=/home/pacmap/.local/share/qualock-antigravity-linux/bin/agy \
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest \
  tests/integration/test_antigravity_real_contract.py -q
```

Expected: PASS. If provider/account availability fails, record it as an external blocker; do not weaken sandbox/hook/auth boundaries.

- [ ] **Step 4: Run full repository gates**

```bash
PYTHONPATH=src /home/pacmap/qualock-easy/.venv/bin/python -m pytest -q
/home/pacmap/qualock-easy/.venv/bin/ruff check src tests
/home/pacmap/qualock-easy/.venv/bin/mypy --strict src
/home/pacmap/qualock-easy/.venv/bin/python -m compileall -q src
git diff --check
git status --short
```

Expected: all tests/gates PASS and only intended Batch #35 files remain tracked/clean after commits.

- [ ] **Step 5: Independent whole-branch review**

Review the branch against `docs/superpowers/specs/2026-09-05-linux-host-agent-runner-antigravity-design.md`, with explicit focus on: no credential copying/reading, no global plugin/settings mutation, fresh profile per attempt, hook default-deny behavior, private `/tmp`, process-tree cleanup, Docker regression, and no silent runtime-image ignore.

- [ ] **Step 6: Commit final tests/docs**

```bash
git add tests/integration/test_antigravity_real_contract.py tests/fixtures/antigravity README.md
git commit -m "test: lock Antigravity Linux host contract"
```

Do not push or open a PR without separate user authorization.
