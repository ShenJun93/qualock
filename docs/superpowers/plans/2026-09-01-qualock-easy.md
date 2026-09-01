# QuaLock Easy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a plain-English safety layer to `qualock check` while preserving all technical verdict and exit-code behavior.

**Architecture:** Introduce a focused `report/safety.py` translation module that converts `QualificationResult` plus canary display names into a `SafetySummary`. Keep `qualification` and policy untouched. The CLI chooses Easy output by default and the existing terminal renderer under `--technical`.

**Tech Stack:** Python 3.11+, dataclasses, Typer, Rich, pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-qualock-easy-design.md`

## Global Constraints
- PASS/WARN/BLOCK/INCOMPLETE policy and exit codes must not change.
- No score, percentage, web UI, or new dependencies.
- Default check output is plain-English; `--technical` preserves the current terminal report.
- Evidence artifacts remain unchanged and must be referenced from Easy output.

---

### Task 1: Safety summary model and mapping

**Files:**
- Create: `src/qualock/report/safety.py`
- Test: `tests/unit/test_safety_report.py`

**Interfaces:**
- Produces: `SafetyStatus`, `WorkflowSafety`, `SafetySummary`, `build_safety_summary(result, display_names)`.

- [ ] Write failing tests for PASS, WARN, BLOCK, and INCOMPLETE mappings, including workflow display names and recommendations.
- [ ] Run `pytest tests/unit/test_safety_report.py -q` and verify RED because the module does not exist.
- [ ] Implement the minimal dataclasses/enums and deterministic mapping.
- [ ] Run the same test file and verify GREEN.

### Task 2: Easy terminal renderer

**Files:**
- Modify: `src/qualock/report/render.py`
- Test: `tests/unit/test_report.py`

**Interfaces:**
- Consumes: `SafetySummary`.
- Produces: `render_safety_terminal(summary, evidence_path)`.

- [ ] Write failing renderer tests asserting headline, workflow names, recommendation, and evidence path.
- [ ] Verify RED.
- [ ] Implement a Rich/plain terminal renderer without color-dependent assertions.
- [ ] Verify GREEN and run report tests.

### Task 3: CLI default and technical mode

**Files:**
- Modify: `src/qualock/cli.py`
- Modify: `src/qualock/commands.py` only if a helper is required to return display names; prefer not to.
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- `qualock check CANDIDATE` prints Easy output.
- `qualock check CANDIDATE --technical` prints the existing technical output.

- [ ] Write failing CLI tests for default Easy output, technical fallback, unchanged BLOCK/INCOMPLETE exits, and evidence-path text.
- [ ] Verify RED.
- [ ] Implement the smallest CLI wiring; load canary names without changing qualification execution.
- [ ] Verify GREEN and run full unit suite.

### Task 4: User-facing documentation and verification

**Files:**
- Modify: `README.md`
- Keep: technical evidence docs unchanged.

- [ ] Replace the first quickstart result example with Easy language and document `--technical`.
- [ ] Run `pytest -q`, `python -m compileall -q src tests`, and `git diff --check`.
- [ ] Review diff for engine/policy/evidence changes; there must be none.
- [ ] Commit and push the feature branch, open PR, require CI 3.11/3.12/3.13 PASS before merge.
