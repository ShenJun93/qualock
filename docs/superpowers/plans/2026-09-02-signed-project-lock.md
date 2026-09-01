# Signed Project Lock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sign project protection locks with an external local key and reject tampered locks before verification commands execute.

**Architecture:** Add a focused signing module that owns key lifecycle, canonical serialization, and HMAC verification. Keep the existing `ProjectLock` model as the signed payload; `io.py` persists a versioned signed envelope. Command orchestration supplies the external key path and converts integrity failures into existing CLI incomplete semantics.

**Tech Stack:** Python 3.11+, hashlib/hmac/secrets/os, platformdirs, Pydantic, pytest.

**Spec:** docs/superpowers/specs/2026-09-02-signed-project-lock-design.md

## Global Constraints
- No secret material is stored in the repository.
- Verify never creates a missing key.
- Signature validation happens before any locked command runs.
- Legacy unsigned locks fail closed.
- Existing Codex qualification behavior remains unchanged.

---

### Task 1: Signing key and envelope primitives
**Files:** create `src/qualock/project_protection/signing.py`; modify `models.py`; add unit tests.
**Produces:** default key path, create/load key, deterministic HMAC sign/verify, signed envelope model.
- [ ] Write RED tests for key creation, deterministic signatures, wrong-key rejection, tampered payload rejection, and legacy-envelope rejection.
- [ ] Implement the minimum signing primitives and make tests GREEN.

### Task 2: Signed project-lock I/O
**Files:** modify `io.py` and `tests/unit/test_project_protection.py`.
**Produces:** `write_project_lock(path, lock, key)` and `read_project_lock(path, key)` that only return authenticated locks.
- [ ] Write RED round-trip and tamper tests.
- [ ] Implement signed persistence and make tests GREEN.

### Task 3: Protect/verify key lifecycle and CLI safety
**Files:** modify `commands.py`, `cli.py`, and flow tests.
**Produces:** protect creates/loads external key; verify loads only; integrity errors exit 4 before any protection execution.
- [ ] Write RED tests for tampered lock, missing key, and proof that tampered definitions never execute.
- [ ] Implement orchestration and user-facing error handling; make tests GREEN.

### Task 4: Docs and release gate
**Files:** modify README and approved design docs only.
- [ ] Document signing, key location, re-protection of legacy locks, local-machine scope, and threat boundary.
- [ ] Run full tests, compileall, diff-check, focused ruff/mypy, and engine-diff audit.
- [ ] Independent Codex review, PR, CI Python 3.11/3.12/3.13, merge, and post-merge verification.
