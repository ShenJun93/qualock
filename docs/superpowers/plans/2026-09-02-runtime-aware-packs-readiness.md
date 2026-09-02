# Runtime-Aware Protection Packs and Environment Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `qualock setup` select the project's intended Python runtime, detect safe framework-specific protections, and distinguish an unready environment from a genuinely failing project without installing dependencies or mutating the project before readiness is confirmed.

**Architecture:** Extend only `qualock.project_setup`. Passive detection produces immutable capabilities, deterministic pack generation uses one central Python command adapter, and a separate readiness module probes only toolchains demanded by proposed protections. `SetupPlan` carries both proposed protections and readiness; only READY plans may reach the existing config writer and `execute_protect`, preserving signed-lock and project-protection semantics.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, pathlib, stdlib `configparser`/`tomllib`/`json`/`shutil`, existing `run_process`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-02-runtime-aware-packs-readiness-design.md`

## Global Constraints

- Capability detection remains passive: never import project modules, execute `setup.py`, run npm scripts, or evaluate project code.
- Readiness may execute only fixed QuaLock-owned inspection probes; never run `uv sync`, `poetry install`, `npm install`, or `npm ci`.
- Runner precedence is exactly: uv, Poetry, valid project-local `.venv`/`venv`, then none.
- Generated Python protections must never fall back to `sys.executable`.
- uv readiness must verify the project environment exists before invoking `uv run`; every uv probe/protection uses `--no-sync`.
- Readiness is demand-driven by proposed protections; irrelevant missing toolchains must not block setup.
- NEEDS SETUP exits through code 4 and performs zero `.qualock` mutation.
- Existing manual `protect`/`verify`, signed lock, evidence, config schema, qualification fingerprints, and agent qualification engine remain unchanged.
- No monorepo traversal, browser checks, shell pipelines, dependency installation, background watch, or automatic verify-after-edit in this batch.

---

## File Structure

- `src/qualock/project_setup/models.py` — runner enum, framework flags, readiness models, `SetupPlan.readiness`.
- `src/qualock/project_setup/detect.py` — passive runtime/framework/dependency detection only.
- `src/qualock/project_setup/runners.py` — one central adapter that builds Python commands from detected runner metadata.
- `src/qualock/project_setup/packs.py` — deterministic protection recommendations using the runner adapter; no environment probing.
- `src/qualock/project_setup/readiness.py` — demand-driven environment probes and actionable recommendations.
- `src/qualock/project_setup/commands.py` — orchestration: detect → recommend → readiness → immutable setup plan; READY gate before mutation.
- `src/qualock/project_setup/render.py` — render stack/readiness/protections in plain language.
- `src/qualock/cli.py` — map NEEDS SETUP to existing exit code 4 without mutation.
- `tests/unit/test_project_setup_detection.py` — passive runner/framework detection.
- `tests/unit/test_project_setup_packs.py` — command construction and framework pack behavior.
- `tests/unit/test_project_setup_readiness.py` — fixed readiness probes and no-install guarantees.
- `tests/unit/test_project_setup_flow.py` — CLI/setup zero-mutation and project-health-vs-readiness behavior.
- `README.md` — runtime-aware setup examples and explicit no-install promise.

---

### Task 1: Passive runtime and framework capabilities

**Files:**
- Modify: `src/qualock/project_setup/models.py`
- Modify: `src/qualock/project_setup/detect.py`
- Modify: `tests/unit/test_project_setup_detection.py`

**Interfaces:**
- Produces `PythonRunner(str, Enum)` with `UV`, `POETRY`, `VENV`, `NONE`.
- Extends `ProjectCapabilities` with `python_runner`, `python_environment`, `django`, `fastapi`, `nextjs`, `typescript`.
- `detect_project(root: Path) -> ProjectCapabilities` remains passive and deterministic.

- [ ] **Step 1: Write RED tests for runner precedence and valid local venv detection**

```python
def test_uv_runner_wins_over_poetry_and_venv(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    (tmp_path / "poetry.lock").write_text("", encoding="utf-8")
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    python = venv / "bin/python"
    python.parent.mkdir(exist_ok=True)
    python.write_text("", encoding="utf-8")

    capabilities = detect_project(tmp_path)

    assert capabilities.python_runner is PythonRunner.UV


def test_local_venv_requires_pyvenv_cfg_and_python(tmp_path: Path) -> None:
    init_git(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").write_text("", encoding="utf-8")

    assert detect_project(tmp_path).python_runner is PythonRunner.NONE
```

- [ ] **Step 2: Run targeted tests and verify RED**

Run:
`PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_project_setup_detection.py -k 'runner or venv'`

Expected: FAIL because `PythonRunner` and runner fields do not exist.

- [ ] **Step 3: Implement runner metadata without executing project binaries**

Add:

```python
class PythonRunner(str, Enum):
    UV = "uv"
    POETRY = "poetry"
    VENV = "venv"
    NONE = "none"
```

Detection rules:
- uv: `uv.lock` or `[tool.uv]` metadata.
- Poetry: `poetry.lock` or `[tool.poetry]` metadata.
- venv: first valid `.venv` then `venv`, requiring `pyvenv.cfg` plus a platform-appropriate Python executable.
- none: no qualifying runner.

Store only a relative project environment path for local venv; do not run the interpreter.

- [ ] **Step 4: Write RED tests for framework labels**

Cover:
- Django requires declared Django dependency plus `manage.py`.
- FastAPI requires declared FastAPI dependency.
- Next.js uses package dependency `next`.
- TypeScript uses dependency `typescript` OR `tsconfig.json`.
- React/Vite behavior remains unchanged.

- [ ] **Step 5: Generalize dependency extraction and make framework tests GREEN**

Refactor metadata parsing into internal helpers that return normalized dependency names from already-supported pyproject/package metadata. Do not add dynamic imports or package-manager execution.

- [ ] **Step 6: Run detection suite**

Run:
`PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_project_setup_detection.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/qualock/project_setup/models.py src/qualock/project_setup/detect.py tests/unit/test_project_setup_detection.py
git commit -m "feat: detect project runtimes and frameworks"
```

---

### Task 2: Central Python runner adapter and runtime-aware packs

**Files:**
- Create: `src/qualock/project_setup/runners.py`
- Modify: `src/qualock/project_setup/packs.py`
- Create/Modify: `tests/unit/test_project_setup_packs.py`

**Interfaces:**
- Produces `python_command(capabilities: ProjectCapabilities, *args: str) -> list[str]`.
- `python_command` raises `ValueError` when no project Python runner is selected; pack generation must omit Python protections rather than fall back to QuaLock's interpreter.
- `recommend_protections(...)` remains deterministic and side-effect free.

- [ ] **Step 1: Write RED tests for exact command prefixes**

```python
def test_uv_python_commands_are_frozen_and_no_sync() -> None:
    capabilities = ProjectCapabilities(python=True, pytest=True, python_runner=PythonRunner.UV)
    command = python_command(capabilities, "-m", "pytest", "-q")
    assert command == ["uv", "run", "--no-sync", "--", "python", "-m", "pytest", "-q"]


def test_poetry_python_command() -> None:
    capabilities = ProjectCapabilities(python=True, python_runner=PythonRunner.POETRY)
    assert python_command(capabilities, "-m", "compileall", "-q", "src")[:3] == ["poetry", "run", "python"]


def test_no_runner_never_uses_qualock_python() -> None:
    capabilities = ProjectCapabilities(python=True, pytest=True, python_runner=PythonRunner.NONE)
    protections = recommend_protections(capabilities, ProtectionLevel.RECOMMENDED)
    assert "pytest" not in {item.id for item in protections}
    assert "python-compile" not in {item.id for item in protections}
```

- [ ] **Step 2: Verify RED**

Run:
`PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_project_setup_packs.py`

Expected: FAIL because `runners.py` does not exist and current packs use `sys.executable`.

- [ ] **Step 3: Implement `python_command` centrally**

Exact prefixes:
- UV: `uv run --no-sync -- python`
- POETRY: `poetry run python`
- VENV: detected project interpreter path
- NONE: raise `ValueError("project Python runner is not available")`

`packs.py` must call this adapter for pytest, compileall, and Django checks; remove `sys.executable` import.

- [ ] **Step 4: Add framework pack tests**

```python
def test_django_recommended_adds_manage_check() -> None:
    capabilities = ProjectCapabilities(
        git=True,
        python=True,
        django=True,
        python_runner=PythonRunner.VENV,
        python_environment=".venv",
    )
    protections = recommend_protections(capabilities, ProtectionLevel.RECOMMENDED)
    django = next(item for item in protections if item.id == "django-check")
    assert django.command[-2:] == ["manage.py", "check"]
```

Also assert FastAPI/Next/React/Vite never create a new convention-based command when the corresponding script/check is absent, and TypeScript only uses an existing `typecheck` npm script at STRONG.

- [ ] **Step 5: Run pack tests and commit**

Expected: PASS.

```bash
git add src/qualock/project_setup/runners.py src/qualock/project_setup/packs.py tests/unit/test_project_setup_packs.py
git commit -m "feat: generate runtime-aware protection packs"
```

---

### Task 3: Demand-driven environment readiness

**Files:**
- Modify: `src/qualock/project_setup/models.py`
- Create: `src/qualock/project_setup/readiness.py`
- Create: `tests/unit/test_project_setup_readiness.py`

**Interfaces:**
- `ReadinessStatus(str, Enum)`: `READY`, `NEEDS_SETUP`.
- Frozen `ReadinessCheck(id, name, status, detail=None, recommendation=None)`.
- Frozen `EnvironmentReadiness(status, checks)`.
- `check_environment_readiness(root: Path, capabilities: ProjectCapabilities, protections: Sequence[ProjectProtectionConfig], *, env: Mapping[str, str] | None = None) -> EnvironmentReadiness`.

- [ ] **Step 1: Write RED uv readiness tests**

Cover exact cases:
- missing `uv` on PATH → NEEDS SETUP with bounded recommendation to install/enable uv, no `uv run` call;
- uv project environment absent → NEEDS SETUP recommending `uv sync`, no `uv run` call;
- `UV_PROJECT_ENVIRONMENT` overrides `.venv`;
- existing environment invokes exactly `uv run --no-sync -- python -c <constant>`;
- failing probe → NEEDS SETUP;
- generic Git-only protections do not probe uv even if project metadata mentions Python.

Use monkeypatch on `shutil.which` and `qualock.project_setup.readiness.run_process` so tests assert no install/sync commands are ever constructed.

- [ ] **Step 2: Implement uv readiness minimally and make uv tests GREEN**

Use a QuaLock-owned constant such as:

```python
PYTHON_PROBE_CODE = "import sys; print(sys.executable)"
```

Before invoking uv, require the environment directory and its platform-appropriate Python executable. Never run uv when that passive existence gate fails.

- [ ] **Step 3: Write RED Poetry/local-venv tests**

Poetry:
- require `poetry` on PATH;
- probe `poetry env info --executable`;
- empty/nonexistent executable path or nonzero/timeout → NEEDS SETUP recommending `poetry install`.

Venv:
- use the selected project interpreter;
- probe only `python -c PYTHON_PROBE_CODE`;
- nonzero/timeout/OSError → NEEDS SETUP.

- [ ] **Step 4: Implement Poetry/local-venv readiness and make tests GREEN**

No dependency installation commands may appear anywhere in `readiness.py`.

- [ ] **Step 5: Write RED npm readiness tests**

Cover:
- proposed npm protection requires both `node` and `npm` on PATH;
- missing `node_modules` → NEEDS SETUP with recommendation to run the project's normal install command;
- no npm-script protections → Node readiness is not probed and does not block setup.

- [ ] **Step 6: Implement npm readiness and aggregate status deterministically**

Order checks consistently: Python tool availability, Python environment, Node availability, Node dependencies. Overall status is NEEDS_SETUP when any emitted check is NEEDS_SETUP; otherwise READY.

- [ ] **Step 7: Run readiness suite and commit**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_project_setup_readiness.py
git add src/qualock/project_setup/models.py src/qualock/project_setup/readiness.py tests/unit/test_project_setup_readiness.py
git commit -m "feat: add project environment readiness checks"
```

---

### Task 4: Wire readiness into setup flow and CLI UX

**Files:**
- Modify: `src/qualock/project_setup/commands.py`
- Modify: `src/qualock/project_setup/render.py`
- Modify: `src/qualock/cli.py`
- Modify: `tests/unit/test_project_setup_flow.py`

**Interfaces:**
- `SetupPlan` contains `capabilities`, `level`, `protections`, `readiness`.
- `build_setup_plan` order: passive detection → Git HEAD preflight → recommendations → readiness.
- `apply_setup_plan` must reject non-READY plans before `ensure_qualock_project` is called.

- [ ] **Step 1: Write RED zero-mutation NEEDS SETUP CLI test**

```python
def test_setup_unready_environment_exits_4_without_mutation(tmp_path: Path, monkeypatch) -> None:
    init_git_with_commit(tmp_path)
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\ndependencies=['pytest>=8']\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("qualock.project_setup.readiness.shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["setup", "--yes"])

    assert result.exit_code == 4
    assert "NEEDS SETUP" in result.stdout
    assert "uv sync" in result.stdout
    assert not (tmp_path / ".qualock").exists()
```

- [ ] **Step 2: Wire readiness into `build_setup_plan` and guard `apply_setup_plan`**

Introduce a small `SetupReadinessError` or equivalent existing setup error mapped to exit 4. The guard happens before config creation, writing, or lock invalidation.

- [ ] **Step 3: Render readiness before recommendations**

READY output must show `Environment` checks with `OK` labels. NEEDS SETUP output must include:
- `QuaLock did not change your project.`
- one concise recommended action from failed readiness checks;
- `Then run: qualock setup`.

Use Rich with literal-safe output as existing Easy paths do; do not parse project-controlled names as markup.

- [ ] **Step 4: Add ready-environment + failing-protection regression test**

Set readiness to READY, make an existing generated protection fail, and assert:
- exit remains existing project-protection failure/incomplete semantics;
- config may remain as today;
- no output claims the environment is unready;
- stale-lock invalidation behavior remains intact.

- [ ] **Step 5: Add cancellation and invalid-config regressions**

Re-run existing zero-mutation tests with readiness present so no `.qualock` directory appears before user acceptance or when existing config validation fails.

- [ ] **Step 6: Run flow suite and commit**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q tests/unit/test_project_setup_flow.py
git add src/qualock/project_setup/commands.py src/qualock/project_setup/render.py src/qualock/cli.py tests/unit/test_project_setup_flow.py
git commit -m "feat: gate project setup on environment readiness"
```

---

### Task 5: Documentation, compatibility, and merge gates

**Files:**
- Modify: `README.md`
- Modify if implementation detail changed: `docs/superpowers/specs/2026-09-02-runtime-aware-packs-readiness-design.md`
- Test: full repository suite and static checks.

**Interfaces:** No new production interfaces; this task proves compatibility and documents the user contract.

- [ ] **Step 1: Update README**

Document:
- runtime-aware detection labels;
- uv/Poetry/local-venv runner precedence;
- NEEDS SETUP example;
- explicit statement that QuaLock does not install/sync dependencies during setup;
- user-owned remediation commands (`uv sync`, `poetry install`, normal npm install command) are recommendations only.

- [ ] **Step 2: Run focused static checks**

```bash
ruff check src/qualock/project_setup tests/unit/test_project_setup_detection.py tests/unit/test_project_setup_packs.py tests/unit/test_project_setup_readiness.py tests/unit/test_project_setup_flow.py
mypy src/qualock/project_setup
```

Expected: no new findings in changed package. Compare any `cli.py` lint findings with `origin/main`; do not widen scope to legacy debt.

- [ ] **Step 3: Run full functional gate**

```bash
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m pytest -q
PYTHONPATH=src /home/pacmap/qualock-exp/.venv/bin/python -m compileall -q src tests
git diff --check
```

Expected: all PASS.

- [ ] **Step 4: Audit engine isolation**

Run:

```bash
git diff origin/main -- src/qualock/qualification src/qualock/run src/qualock/evidence src/qualock/commands
```

Expected: empty diff. If runtime helpers must touch `qualock.run.process`, stop and re-review scope rather than silently widening it.

- [ ] **Step 5: Independent review**

Review exact feature diff for Critical/Important findings, specifically:
- any readiness probe that can install/sync/execute app code;
- any fallback to QuaLock's interpreter;
- uv invocation before environment existence gate;
- readiness blocking irrelevant toolchains;
- zero-mutation violations;
- invented framework commands.

Fix all Critical/Important findings TDD-first and rerun gates.

- [ ] **Step 6: Open PR #23 and require GitHub CI**

Push final head, open PR against `main`, and require Python 3.11/3.12/3.13 CI success on exact head before merge.

- [ ] **Step 7: Squash merge and verify `main`**

Merge only with expected head SHA. Then verify remote `main`, run a fresh post-merge test checkout, and confirm push-CI success on the merge commit. Do not create a release or publish PyPI in this batch.
