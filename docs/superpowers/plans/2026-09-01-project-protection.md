# Project Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add project-local protect/verify regression checks without changing Codex qualification semantics.

**Architecture:** Extend config schema v1 with optional project protections. A focused project_protection package runs commands, records a known-good lock and evidence, compares current results, and renders an Easy report. CLI wiring remains separate from the existing qualification engine.

**Tech Stack:** Python 3.11+, Pydantic, dataclasses, Typer, pytest.

**Spec:** docs/superpowers/specs/2026-09-01-project-protection-design.md

## Global Constraints
- Existing config files without protections remain valid.
- Commands are argv lists and execute without a shell.
- Protect writes a lock only when every baseline check passes.
- Verify uses locked definitions, not current mutable config.
- Existing qualification policy/evidence/exit behavior must not change.

---

### Task 1: Protection config schema
**Files:** modify src/qualock/config/models.py and tests/unit/test_config.py.
**Produces:** ProjectProtectionConfig and QualockConfig.protections.
- [ ] Write tests proving old config remains valid and protection entries validate id, name, command, timeout.
- [ ] Run targeted tests and confirm RED.
- [ ] Implement minimal Pydantic models.
- [ ] Run targeted tests and confirm GREEN.

### Task 2: Protection runner and lock
**Files:** create src/qualock/project_protection/models.py, runner.py, io.py; create tests/unit/test_project_protection.py.
**Produces:** run_protections(root, definitions), create_project_lock, read/write lock, Git provenance helpers.
- [ ] Write failing tests for pass/fail/timeout, all-pass lock creation, and lock round trip.
- [ ] Confirm RED, implement minimal code, confirm GREEN.

### Task 3: Protect and verify commands
**Files:** create src/qualock/project_protection/commands.py; modify src/qualock/cli.py; extend tests/unit/test_cli.py.
**Produces:** qualock protect and qualock verify with exit codes 0/2/3/4/1 consistent with current CLI conventions.
- [ ] Write CLI tests first for successful protect, refused baseline, safe verify, regression verify, incomplete verify.
- [ ] Confirm RED, implement minimal command orchestration, confirm GREEN.

### Task 4: Easy project report and evidence
**Files:** create src/qualock/project_protection/render.py and storage.py; add tests.
**Produces:** SAFE TO KEEP / DON'T KEEP THIS CHANGE / CHECK COULD NOT FINISH output and JSON artifacts.
- [ ] Write renderer/storage tests first, confirm RED.
- [ ] Implement deterministic report and evidence storage, confirm GREEN.

### Task 5: Docs and release gate
**Files:** modify README.md; keep existing evidence docs unchanged.
- [ ] Document protections config, protect, verify, and technical artifact location.
- [ ] Run full pytest, compileall, diff-check, focused static checks, and engine-diff audit.
- [ ] Commit/push branch, open PR, get independent Codex review, require CI Python 3.11/3.12/3.13 PASS, then merge.
