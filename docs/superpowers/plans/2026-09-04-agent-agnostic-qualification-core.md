# Agent-Agnostic Qualification Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Codex-specific execution, credential, evidence, and report assumptions from the local qualification core while preserving current Codex behavior and artifacts.

**Architecture:** The agent adapter owns a context-managed invocation plus evidence parsing. `DockerQualificationBackend` consumes only normalized invocation/evidence contracts, while `DockerRunner` receives an adapter-selected container binary path. Report APIs receive an injected display name; top-level command wiring remains Codex-only in this batch.

**Tech Stack:** Python 3.11+, dataclasses, context managers, Pydantic existing models, Typer, Docker CLI orchestration, pytest, Ruff, strict mypy.

**Spec:** `docs/superpowers/specs/2026-09-04-agent-agnostic-qualification-core-design.md`

## Global Constraints

- Add no second public agent in Batch #30.
- Keep `codex@<version>` as the only accepted public agent spec.
- Keep `baseline.lock` schema version 1 unchanged.
- Keep successful Codex `baseline.json`, `report.json`, `qualification.json`, and `report.md` content/schema unchanged.
- Keep qualification policy, pairing/interleaving, canary grading, and integrity decisions unchanged.
- Keep release monitor, version bisect, scheduler, and GitHub PR qualification explicitly Codex-only.
- Do not modify `pyproject.toml` or add dependencies.
- Credential source files must never be mounted directly into the writable container auth home.
- Use TDD: each production behavior begins with a failing test that fails for the intended reason.

---

### Task 1: Normalize agent evidence without changing Codex parsing

**Files:**
- Create: `src/qualock/evidence/models.py`
- Modify: `src/qualock/evidence/codex_jsonl.py:1-104`
- Modify: `tests/unit/test_codex_jsonl.py:1-45`

**Interfaces:**
- Produces: `AgentEvidenceError`, `CommandEvent`, and `AgentEvidence` in `qualock.evidence.models`.
- Produces: `parse_codex_jsonl(lines: Iterable[str]) -> AgentEvidence`.
- Preserves: `CodexEvidenceError`, now a subclass of `AgentEvidenceError`.

- [ ] **Step 1: Write the failing generic-evidence assertions**

Add imports and assertions to `tests/unit/test_codex_jsonl.py`:

```python
from qualock.evidence.models import AgentEvidence, AgentEvidenceError


def test_codex_parser_returns_normalized_agent_evidence() -> None:
    evidence = parse_codex_jsonl(['{"type":"turn.completed","usage":{"input_tokens":2}}'])
    assert isinstance(evidence, AgentEvidence)
    assert evidence.input_tokens == 2


def test_codex_parse_error_is_generic_agent_evidence_error() -> None:
    with pytest.raises(AgentEvidenceError):
        parse_codex_jsonl(["not-json"])
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_codex_jsonl.py
```

Expected: import failure because `qualock.evidence.models` does not exist.

- [ ] **Step 3: Add the normalized evidence model**

Create `src/qualock/evidence/models.py` with:

```python
from dataclasses import dataclass, field
from typing import Any


class AgentEvidenceError(ValueError):
    pass


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

Refactor `codex_jsonl.py` to import `AgentEvidence`, `AgentEvidenceError`, and `CommandEvent`; define:

```python
class CodexEvidenceError(AgentEvidenceError):
    pass
```

and instantiate `AgentEvidence()` in `parse_codex_jsonl`.

- [ ] **Step 4: Run focused evidence tests GREEN**

Run the Task 1 pytest command again. Expected: all pass.

- [ ] **Step 5: Run static checks and commit**

```bash
/tmp/qualock-static-22-final/bin/ruff check src/qualock/evidence tests/unit/test_codex_jsonl.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/evidence
git diff --check
git add src/qualock/evidence/models.py src/qualock/evidence/codex_jsonl.py tests/unit/test_codex_jsonl.py
git commit -m "refactor: normalize agent evidence"
```

---

### Task 2: Give adapters ownership of context-managed invocation and Codex auth

**Files:**
- Modify: `src/qualock/agents/base.py:1-49`
- Modify: `src/qualock/agents/codex.py:1-67`
- Modify: `tests/unit/test_codex_adapter.py:1-70`

**Interfaces:**
- Consumes: `AgentEvidence` from Task 1.
- Produces: `AgentMount` and `AgentInvocation` dataclasses.
- Produces protocol methods `invocation(...) -> ContextManager[AgentInvocation]` and `parse_evidence(stdout, stderr) -> AgentEvidence`.
- Produces `CodexAdapter(auth_home: Path | None = None)` that owns secure auth materialization.

- [ ] **Step 1: Add failing invocation-contract tests**

Extend `tests/unit/test_codex_adapter.py` with tests equivalent to:

```python
def test_invocation_preserves_codex_container_path_and_argv(tmp_path: Path, monkeypatch) -> None:
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "detect_capabilities", lambda path: AgentCapabilities(True, True, True, True, True, True, True))
    binary = AgentBinary("codex", "0.150.0", tmp_path / "codex", "sha")
    with adapter.invocation(binary, model="gpt-5.3-codex", reasoning_effort="high", prompt="Fix it") as invocation:
        assert invocation.argv[0] == str(binary.path)
        assert invocation.container_binary_path == "/opt/qualock/codex"


def test_invocation_owns_temporary_codex_auth_seed(tmp_path: Path, monkeypatch) -> None:
    auth_home = tmp_path / "codex-home"
    auth_home.mkdir()
    (auth_home / "auth.json").write_text('{"token":"test-only"}', encoding="utf-8")
    adapter = CodexAdapter(auth_home=auth_home)
    monkeypatch.setattr(adapter, "detect_capabilities", lambda path: AgentCapabilities(True, True, True, True, True, True, True))
    binary = AgentBinary("codex", "0.150.0", tmp_path / "codex", "sha")
    with adapter.invocation(binary, model="gpt-5.3-codex", reasoning_effort="high", prompt="Fix it") as invocation:
        seed = next(mount.host_path for mount in invocation.mounts if mount.container_path == "/opt/qualock/auth-seed.json")
        assert seed.read_text(encoding="utf-8") == '{"token":"test-only"}'
        assert invocation.environment == (("CODEX_HOME", "/opt/qualock/auth"),)
        assert invocation.tmpfs_mounts == ("/opt/qualock/auth",)
        assert invocation.bootstrap_copy == ("/opt/qualock/auth-seed.json", "/opt/qualock/auth/auth.json")
    assert not seed.exists()
```

Also add a test that `CodexAdapter.parse_evidence(...)` returns normalized usage from Codex JSONL.

- [ ] **Step 2: Run adapter tests RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_codex_adapter.py
```

Expected: missing `AgentInvocation`, `AgentMount`, adapter constructor/invocation methods.

- [ ] **Step 3: Implement the invocation contract in `agents/base.py`**

Add:

```python
from contextlib import AbstractContextManager
from typing import Literal
from qualock.evidence.models import AgentEvidence

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

Replace the old minimal `AgentAdapter` protocol with the two methods defined by the spec.

- [ ] **Step 4: Implement Codex invocation and evidence parsing**

Use `contextlib.contextmanager`, `tempfile.TemporaryDirectory`, and `shutil.copy2` in `CodexAdapter.invocation`. Keep `detect_capabilities` and `build_exec_argv` behavior unchanged. If `auth_home` is not `None`, always set `CODEX_HOME=/opt/qualock/auth` and request tmpfs `/opt/qualock/auth`; only the read-only seed mount and bootstrap copy depend on `auth.json` being present.

`parse_evidence` must call:

```python
return parse_codex_jsonl(stdout.splitlines())
```

Do not parse stderr as JSONL.

- [ ] **Step 5: Run adapter/evidence tests GREEN and static checks**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_codex_adapter.py tests/unit/test_codex_jsonl.py
/tmp/qualock-static-22-final/bin/ruff check src/qualock/agents src/qualock/evidence tests/unit/test_codex_adapter.py tests/unit/test_codex_jsonl.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/agents src/qualock/evidence
```

- [ ] **Step 6: Commit**

```bash
git diff --check
git add src/qualock/agents/base.py src/qualock/agents/codex.py tests/unit/test_codex_adapter.py
git commit -m "refactor: move Codex runtime into adapter"
```

---

### Task 3: Make Docker binary placement adapter-selectable

**Files:**
- Modify: `src/qualock/run/docker.py:75-210`
- Modify: `tests/unit/test_docker_commands.py:1-190`

**Interfaces:**
- Produces: `agent_container_path: str = "/opt/qualock/agent"` on `build_agent_create_argv` and `run_agent`.
- Validation: path must be absolute when `replace_agent_binary=True`.

- [ ] **Step 1: Add failing Docker argv tests**

Add:

```python
def test_agent_binary_can_use_adapter_selected_container_path(tmp_path: Path) -> None:
    runner = DockerRunner()
    binary = tmp_path / "agent"
    binary.write_text("x", encoding="utf-8")
    argv = runner.build_agent_create_argv(
        prepared_image="sha256:prepared",
        container_name="q1",
        agent_binary=binary,
        agent_argv=[str(binary), "run"],
        environment={},
        agent_container_path="/opt/qualock/claude",
    )
    assert f"{binary.resolve()}:/opt/qualock/claude:ro" in argv
    assert argv[-2:] == ["/opt/qualock/claude", "run"]


def test_agent_container_path_must_be_absolute(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="agent container path must be absolute"):
        DockerRunner().build_agent_create_argv(
            prepared_image="p", container_name="q", agent_binary=tmp_path / "a",
            agent_argv=["a"], environment={}, agent_container_path="relative/agent"
        )
```

- [ ] **Step 2: Run Docker command tests RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_docker_commands.py
```

- [ ] **Step 3: Implement generic path forwarding**

Add the parameter to both methods, validate with `startswith("/")`, mount the binary at that path, rewrite `command[0]`, and forward it from `run_agent` to `build_agent_create_argv`.

Change only the Dockerfile failure copy from `Qualock Codex runner` to `Qualock agent runner`.

- [ ] **Step 4: Run Docker tests GREEN and static checks**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_docker_commands.py
/tmp/qualock-static-22-final/bin/ruff check src/qualock/run/docker.py tests/unit/test_docker_commands.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/run/docker.py
```

- [ ] **Step 5: Commit**

```bash
git diff --check
git add src/qualock/run/docker.py tests/unit/test_docker_commands.py
git commit -m "refactor: generalize agent binary placement"
```

---

### Task 4: Refactor the qualification backend to consume only generic adapter contracts

**Files:**
- Modify: `src/qualock/run/backend.py:1-232`
- Modify: `src/qualock/commands.py:1-190`
- Modify: `tests/unit/test_docker_backend.py:1-230`
- Modify as needed: `tests/unit/test_commands.py`

**Interfaces:**
- Consumes: `AgentAdapter`, `AgentInvocation`, `AgentMount`, `AgentEvidenceError`.
- `DockerQualificationBackend(..., agent_adapter: AgentAdapter, ...)` has no `auth_home` argument.
- `_default_backend` constructs `CodexAdapter(auth_home=...)` and passes it as `agent_adapter`.

- [ ] **Step 1: Rewrite backend fake as a generic adapter and add failing assertions**

In `test_docker_backend.py`, replace `FakeAdapter.detect_capabilities/build_exec_argv` with a context-managed invocation and normalized evidence. The fake must not emit Codex JSON to make the backend pass:

```python
class FakeAdapter:
    @contextmanager
    def invocation(self, binary: AgentBinary, *, model: str, reasoning_effort: str, prompt: str):
        yield AgentInvocation(argv=(str(binary.path), "run", prompt), container_binary_path="/opt/qualock/fake")

    def parse_evidence(self, stdout: str, stderr: str) -> AgentEvidence:
        return AgentEvidence(input_tokens=12, output_tokens=3)
```

Add assertions that FakeDocker receives `/opt/qualock/fake`, and add an adapter variant whose `parse_evidence` raises `AgentEvidenceError("bad evidence")`; verify the attempt becomes invalid and the grader is not called.

Keep secure Codex auth assertions out of this backend test; Task 2 owns them.

- [ ] **Step 2: Run backend tests RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_docker_backend.py tests/unit/test_commands.py
```

Expected: constructor/protocol mismatch because backend still requires Codex methods/auth.

- [ ] **Step 3: Implement agent-neutral backend orchestration**

Remove `shutil`, `tempfile`, `AgentCapabilities`, and Codex parser imports from `backend.py`.

Within `run_attempt`:

```python
with self.agent_adapter.invocation(
    binary,
    model=self.model,
    reasoning_effort=self.reasoning_effort,
    prompt=canary.task,
) as invocation:
    mounts = [(m.host_path, m.container_path, m.mode) for m in invocation.mounts]
    mounts.extend((s.path, s.container_path, "ro") for s in binary.support_binaries)
    state = self.docker_runner.run_agent(
        ...,
        agent_argv=invocation.argv,
        environment=dict(invocation.environment),
        extra_mounts=mounts,
        tmpfs_mounts=invocation.tmpfs_mounts,
        bootstrap_copy=invocation.bootstrap_copy,
        agent_container_path=invocation.container_binary_path,
        ...,
    )
```

Parse using `self.agent_adapter.parse_evidence(state.stdout, state.stderr)` and catch `AgentEvidenceError`.

Keep the existing `finally: remove_container(...)` behavior around all post-run validation/grading paths.

- [ ] **Step 4: Update default Codex wiring**

In `commands._default_backend`, move `auth_home` into `CodexAdapter(auth_home=...)`; pass `agent_adapter=...` and remove backend `auth_home=`.

Do not change `parse_agent_spec`, resolver selection, or `AgentPin(name="codex", ...)`.

- [ ] **Step 5: Run backend/command tests GREEN plus security regressions**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_docker_backend.py \
  tests/unit/test_codex_adapter.py \
  tests/unit/test_commands.py \
  tests/unit/test_integrity.py
/tmp/qualock-static-22-final/bin/ruff check src/qualock/run/backend.py src/qualock/commands.py tests/unit/test_docker_backend.py tests/unit/test_commands.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock/run/backend.py src/qualock/commands.py
```

- [ ] **Step 6: Assert backend contains no Codex leakage and commit**

```bash
! grep -nE 'Codex|CODEX_HOME|auth\.json|codex_jsonl|codex_adapter' src/qualock/run/backend.py
git diff --check
git add src/qualock/run/backend.py src/qualock/commands.py tests/unit/test_docker_backend.py tests/unit/test_commands.py
git commit -m "refactor: make qualification backend agent-neutral"
```

---

### Task 5: Inject agent presentation into reports while preserving Codex artifacts

**Files:**
- Modify: `src/qualock/report/render.py:21-108`
- Modify: `src/qualock/report/safety.py:28-110`
- Modify: `src/qualock/evidence/storage.py:40-68`
- Modify: `src/qualock/commands.py:180-184`
- Modify: `src/qualock/cli.py:96-198`
- Modify: `tests/unit/test_report.py`
- Modify: `tests/unit/test_safety_report.py`
- Modify: `tests/unit/test_storage.py`
- Modify as needed: `tests/unit/test_cli.py`

**Interfaces:**
- `render_markdown(result, *, agent_display_name: str)`
- `render_terminal(result, *, agent_display_name: str)`
- `build_safety_summary(result, display_names, *, agent_display_name: str)`
- `write_qualification_artifacts(base_dir, result, *, agent_display_name: str)`
- `SafetySummary.agent_display_name: str`

- [ ] **Step 1: Add failing synthetic-agent report tests**

Add tests that render with `agent_display_name="Claude Code"` and assert:

```python
assert "Claude Code 0.150.0 -> 0.151.0" in render_terminal(
    sample_result(), agent_display_name="Claude Code"
)
assert "Codex" not in render_terminal(sample_result(), agent_display_name="Claude Code")
```

For safety, build with `agent_display_name="Claude Code"` and assert the recommendation and version line say `Claude Code`, not `Codex`.

For storage, call `write_qualification_artifacts(..., agent_display_name="Codex")` and capture current fixture content expectations so `report.json` and `qualification.json` remain unchanged.

- [ ] **Step 2: Run report/storage tests RED**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_report.py tests/unit/test_safety_report.py tests/unit/test_storage.py tests/unit/test_cli.py
```

- [ ] **Step 3: Implement display-name injection**

Use `agent_display_name` in technical markdown/terminal and safety recommendations/version line. Add the field to `SafetySummary`. Do not add it to `QualificationResult` and do not change `render_json`.

- [ ] **Step 4: Wire Codex presentation at the top level**

In `commands.execute_check`, call:

```python
write_qualification_artifacts(
    project_dir(root) / "results",
    result,
    agent_display_name="Codex",
)
```

In CLI technical and safety paths, pass `agent_display_name="Codex"` explicitly.

Do not alter monitor/version-bisect/GitHub PR copy in this task.

- [ ] **Step 5: Run report/storage/CLI tests GREEN**

Run the Task 5 pytest command again, then:

```bash
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/report src/qualock/evidence/storage.py src/qualock/commands.py src/qualock/cli.py \
  tests/unit/test_report.py tests/unit/test_safety_report.py tests/unit/test_storage.py tests/unit/test_cli.py
/tmp/qualock-static-22-final/bin/mypy --strict \
  src/qualock/report src/qualock/evidence/storage.py src/qualock/commands.py src/qualock/cli.py
```

- [ ] **Step 6: Commit**

```bash
git diff --check
git add src/qualock/report src/qualock/evidence/storage.py src/qualock/commands.py src/qualock/cli.py \
  tests/unit/test_report.py tests/unit/test_safety_report.py tests/unit/test_storage.py tests/unit/test_cli.py
git commit -m "refactor: inject agent presentation into reports"
```

---

### Task 6: Final regression gate and scope proof

**Files:**
- No production feature expansion.
- Modify tests only if a final regression test is required to prove a spec invariant discovered during the gate.

**Interfaces:**
- Verifies the complete Batch #30 contract and non-goals.

- [ ] **Step 1: Run full test suite**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q -rs
```

Expected: all tests pass; count must be at least the baseline 764 plus new Batch #30 tests.

- [ ] **Step 2: Run focused security/core/report subset**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q \
  tests/unit/test_codex_adapter.py \
  tests/unit/test_codex_jsonl.py \
  tests/unit/test_docker_backend.py \
  tests/unit/test_docker_commands.py \
  tests/unit/test_integrity.py \
  tests/unit/test_report.py \
  tests/unit/test_safety_report.py \
  tests/unit/test_storage.py \
  tests/unit/test_commands.py \
  tests/unit/test_cli.py
```

- [ ] **Step 3: Run final static gates**

```bash
/tmp/qualock-static-22-final/bin/ruff check \
  src/qualock/agents src/qualock/evidence src/qualock/run src/qualock/report \
  src/qualock/commands.py src/qualock/cli.py \
  tests/unit/test_codex_adapter.py tests/unit/test_codex_jsonl.py \
  tests/unit/test_docker_backend.py tests/unit/test_docker_commands.py \
  tests/unit/test_report.py tests/unit/test_safety_report.py tests/unit/test_storage.py \
  tests/unit/test_commands.py tests/unit/test_cli.py
/tmp/qualock-static-22-final/bin/mypy --strict src/qualock
/home/pacmap/qualock-exp/.venv/bin/python -m compileall -q src tests
git diff --check origin/main...HEAD
```

- [ ] **Step 4: Prove scope/non-goals**

```bash
! git diff --name-only origin/main...HEAD -- pyproject.toml | grep .
git diff --exit-code origin/main...HEAD -- \
  src/qualock/release_monitor \
  src/qualock/version_bisect \
  src/qualock/github_pr \
  src/qualock/scheduler
! grep -nE 'Codex|CODEX_HOME|auth\.json|codex_jsonl|codex_adapter' src/qualock/run/backend.py
```

Also verify the expected intentional Codex-only top-level guards still exist:

```bash
grep -n 'v0.1 supports only codex' src/qualock/commands.py
grep -n 'release monitor supports only a Codex baseline' src/qualock/release_monitor/commands.py
grep -n 'version bisect supports only a Codex baseline' src/qualock/version_bisect/commands.py
```

- [ ] **Step 5: Review diff for accidental artifact/schema changes**

Compare `tests/unit/test_storage.py` expectations and report tests against pre-batch behavior. `render_json` and `QualificationResult` must have no new presentation field.

- [ ] **Step 6: Commit any test-only gate fix if needed**

Only if Step 1-5 required an additional regression test:

```bash
git add tests
git commit -m "test: lock agent-neutral core regressions"
```

Otherwise do not create an empty commit.
