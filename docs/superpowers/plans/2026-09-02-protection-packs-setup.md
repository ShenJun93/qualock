# Protection Packs and Setup Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `qualock setup` with deterministic project detection and built-in protection recommendations that feed the existing signed project-protection engine.

**Architecture:** A new `project_setup` package owns passive detection, pack recommendation, config mutation, and setup orchestration. It produces ordinary `ProjectProtectionConfig` values and delegates execution to `execute_protect`, preserving the current protection and signing engine.

**Tech Stack:** Python 3.11+, pathlib/json/tomllib, Pydantic models already in QuaLock, Typer/Rich, PyYAML, pytest.

**Spec:** docs/superpowers/specs/2026-09-02-protection-packs-setup-design.md

## Global Constraints
- Detection never executes project code.
- V1 recognizes Git, Python, pytest, Node/npm, React, Vite, and npm scripts `test`, `build`, `lint`, `typecheck`.
- `--level` is one of `minimal`, `recommended`, `strong`; default is `recommended`.
- `--yes` is the non-interactive confirmation override.
- Existing config mutation changes only the `protections` top-level key.
- Setup delegates baseline execution and signing to the existing `execute_protect`.
- No marketplace, browser automation, dependency installation, or shell pipeline support.

---

### Task 1: Passive project detector and pack registry
**Files:**
- Create: `src/qualock/project_setup/__init__.py`
- Create: `src/qualock/project_setup/models.py`
- Create: `src/qualock/project_setup/detect.py`
- Create: `src/qualock/project_setup/packs.py`
- Test: `tests/unit/test_project_setup_detection.py`

**Interfaces:**
- `detect_project(root: Path) -> ProjectCapabilities`
- `recommend_protections(capabilities: ProjectCapabilities, level: ProtectionLevel) -> tuple[ProjectProtectionConfig, ...]`

- [ ] Write RED tests for Python+pytest detection, React+Vite npm metadata, generic Git fallback, level filtering, npm script filtering, deterministic deduplication, and unsupported non-Git directories.
- [ ] Run the detector tests and confirm they fail because `qualock.project_setup` does not exist.
- [ ] Implement immutable capability/level models, passive metadata parsing, and deterministic protection generation.
- [ ] Run detector tests and confirm GREEN.
- [ ] Commit as `feat: add built-in protection pack detection`.

### Task 2: Safe protection config writer
**Files:**
- Create: `src/qualock/project_setup/config.py`
- Test: `tests/unit/test_project_setup_config.py`

**Interfaces:**
- `ensure_qualock_project(root: Path) -> Path`
- `write_protections(config_path: Path, protections: Sequence[ProjectProtectionConfig]) -> None`

- [ ] Write RED tests proving setup creates a default `.qualock/config.yaml`, preserves unrelated/unknown YAML keys, replaces only `protections`, and writes valid `QualockConfig`.
- [ ] Run tests and confirm RED because config helpers do not exist.
- [ ] Implement directory/default-config creation and raw-YAML protection replacement with post-write validation.
- [ ] Run config tests and confirm GREEN.
- [ ] Commit as `feat: add safe setup config writer`.

### Task 3: Setup orchestration and CLI
**Files:**
- Create: `src/qualock/project_setup/commands.py`
- Create: `src/qualock/project_setup/render.py`
- Modify: `src/qualock/cli.py`
- Test: `tests/unit/test_project_setup_flow.py`

**Interfaces:**
- `build_setup_plan(root: Path, level: ProtectionLevel) -> SetupPlan`
- `apply_setup_plan(root: Path, plan: SetupPlan) -> ProjectProtectResult`
- CLI: `qualock setup [--level LEVEL] [--yes]`

- [ ] Write RED flow tests for friendly detection output, confirmation cancellation with zero mutations, `--yes` config generation, successful signed lock creation, failing baseline exit 4, and unsupported project exit 3.
- [ ] Run flow tests and confirm RED because setup command/orchestration is absent.
- [ ] Implement plan rendering, one confirmation prompt, config write, delegation to `execute_protect`, and existing protect-result rendering/exit semantics.
- [ ] Run setup flow tests and confirm GREEN.
- [ ] Commit as `feat: add qualock setup wizard`.

### Task 4: Documentation and merge gate
**Files:**
- Modify: `README.md`
- Keep: approved spec and plan docs

- [ ] Document one-command setup, detected project examples, protection levels, `--yes`, and explicit V1 non-claims.
- [ ] Run full pytest, compileall, `git diff --check`, focused ruff/mypy, and audit that qualification/run/evidence/fingerprint files are unchanged.
- [ ] Run independent reviewer against `origin/main..HEAD`; fix any Critical/Important findings with regression tests.
- [ ] Push PR #22, require CI Python 3.11/3.12/3.13 success, squash merge, then verify `main` and post-merge CI.
