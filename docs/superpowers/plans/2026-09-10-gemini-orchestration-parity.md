# Gemini Orchestration Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend Gemini CLI from local qualification into release monitoring, native scheduled monitoring, forward version bisect, and GitHub baseline-upgrade PR qualification without weakening support-tree identity, credential isolation, or existing Codex/Claude behavior.

**Architecture:** Add one pure orchestration capability lookup for support facts, then reuse the existing release-monitor, scheduler, bisect, and GitHub PR engines. Gemini-specific mechanics remain in the Gemini resolver/auth paths; schemas stay unchanged and Antigravity remains fail-closed.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, pytest, Ruff, mypy, GitHub Actions YAML templates, npm-backed resolver probes.

**Spec:** `docs/superpowers/specs/2026-09-10-gemini-orchestration-parity-design.md`

## Global Constraints

- Base is `45a2250135317601acedea4d15bcb4632586659d` after PR #41.
- No dependency install/fetch as part of implementation; use the canonical existing venv.
- No authenticated Gemini provider run unless separately authorized.
- No push, PR, merge, tag, release, or publish without explicit operator authorization.
- Baseline schema stays `1`; monitor state stays `1`; bisect summary stays `2`; PR context/report stay `2`; scheduler registration shape stays unchanged.
- Only `GEMINI_API_KEY` is a Gemini automation credential; never persist it in scheduler/monitor/bisect/PR artifacts.
- Codex/Claude behavior must remain unchanged; Antigravity and unknown agents remain unsupported for these orchestration surfaces.
- Each production task requires observed RED, minimal GREEN, focused regression gates, independent scoped review, then a local commit.

---
## File map

- Create `src/qualock/agents/orchestration.py`: immutable orchestration capability facts and a pure lookup.
- Modify `src/qualock/agents/gemini_resolver.py`: exact-stable explicit versions plus stable npm catalog.
- Modify `src/qualock/agents/releases.py`: Gemini latest/stable adapters and existing error translation.
- Modify `src/qualock/release_monitor/{models.py,commands.py}` and `src/qualock/cli.py`: Gemini monitor/state parity.
- Verify `src/qualock/scheduler/{commands.py,runner.py,models.py,backends/*}` without adding Gemini-specific backend branches.
- Modify `src/qualock/version_bisect/{models.py,commands.py}`: Gemini forward-scan parity.
- Modify `src/qualock/github_pr/{models.py,commands.py,publisher.py,templates.py}` and `src/qualock/cli.py`: PR identity, resolver, workflow/auth, display/setup parity.
- Tests remain in the existing unit modules for each owner; add no new dependency or broad helper fixture unless two task groups genuinely share it.

### Task 1: Shared capabilities and stable Gemini release discovery

**Files:**
- Create: `src/qualock/agents/orchestration.py`
- Modify: `src/qualock/agents/gemini_resolver.py`
- Modify: `src/qualock/agents/releases.py`
- Test: `tests/unit/test_gemini_resolver.py`
- Test: `tests/unit/test_agent_releases.py`
- Create or Test: `tests/unit/test_agent_orchestration.py`

**Interfaces:**
- Produces: `OrchestrationCapabilities(release_discovery: bool, version_bisect: bool, github_pr: bool)`.
- Produces: `orchestration_capabilities(agent_name: str) -> OrchestrationCapabilities`.
- Produces: `GeminiResolver.stable_versions() -> tuple[str, ...]`.
- Preserves: `default_latest_release_source()` / `default_stable_release_catalog()` protocols and `ReleaseDiscoveryError` boundary.
- [ ] **Step 1.1: Add RED capability-matrix tests**

```python
@pytest.mark.parametrize("agent", ["codex", "claude", "gemini"])
def test_orchestration_capabilities_support_mainline_agents(agent: str) -> None:
    caps = orchestration_capabilities(agent)
    assert (caps.release_discovery, caps.version_bisect, caps.github_pr) == (True, True, True)

@pytest.mark.parametrize("agent", ["antigravity", "other", ""])
def test_orchestration_capabilities_fail_closed(agent: str) -> None:
    caps = orchestration_capabilities(agent)
    assert (caps.release_discovery, caps.version_bisect, caps.github_pr) == (False, False, False)
```

Run: `pytest -q tests/unit/test_agent_orchestration.py`
Expected: RED because the module/API does not exist.

- [ ] **Step 1.2: Add RED Gemini stable-version tests**

Cover explicit `0.59.0-preview.1`, `0.59.0+build`, nightly strings, malformed values, and prove failure occurs before host Node/package/runtime probes. Add `stable_versions()` tests for numeric ordering, deduplication, malformed JSON, non-list/non-string payloads, npm timeout/nonzero, and scrubbed Gemini/Google credential env.

Run: `pytest -q tests/unit/test_gemini_resolver.py -k 'stable_versions or prerelease or preview or build'`
Expected: RED because explicit broad semver is still accepted and no stable catalog exists.
- [ ] **Step 1.3: Implement minimal capability and release code**

```python
@dataclass(frozen=True)
class OrchestrationCapabilities:
    release_discovery: bool = False
    version_bisect: bool = False
    github_pr: bool = False

_SUPPORTED = OrchestrationCapabilities(True, True, True)
_UNSUPPORTED = OrchestrationCapabilities()

def orchestration_capabilities(agent_name: str) -> OrchestrationCapabilities:
    return _SUPPORTED if agent_name in {"codex", "claude", "gemini"} else _UNSUPPORTED
```

In `GeminiResolver`, make both explicit resolution and registry stable discovery use exact `X.Y.Z` validation. Implement `stable_versions()` with the existing npm probe environment, JSON list validation, stable filtering, dedupe, and numeric tuple sort. Add Gemini wrappers to `agents/releases.py` translating `GeminiResolveError` to `ReleaseDiscoveryError`.

- [ ] **Step 1.4: Run GREEN and regressions**

Run:
`pytest -q tests/unit/test_agent_orchestration.py tests/unit/test_gemini_resolver.py tests/unit/test_agent_releases.py`

Then:
`ruff check src/qualock/agents/orchestration.py src/qualock/agents/gemini_resolver.py src/qualock/agents/releases.py tests/unit/test_agent_orchestration.py tests/unit/test_gemini_resolver.py tests/unit/test_agent_releases.py`

Expected: PASS; existing Codex/Claude/Antigravity release tests unchanged.

- [ ] **Step 1.5: Independent scoped review, fix loop if C/I, then commit**

Review exact Task 1 diff against spec Required TDD items 1–4 and final-review prerelease Minor. Commit only after `SPEC PASS`, `C0/I0`:
`git commit -m "feat: add Gemini stable release discovery"`.

---
### Task 2: Release monitor and state parity

**Files:**
- Modify: `src/qualock/release_monitor/models.py`
- Modify: `src/qualock/release_monitor/commands.py`
- Modify: `src/qualock/cli.py`
- Test: `tests/unit/test_release_monitor_state.py`
- Test: `tests/unit/test_release_monitor_flow.py`
- Test: existing monitor CLI tests in `tests/unit/`

**Interfaces:**
- Consumes: `orchestration_capabilities(agent_name)` from Task 1.
- Produces: `MonitorAgent = Literal["codex", "claude", "gemini"]` and schema-v1 Gemini monitor state.
- Preserves: existing monitor action/verdict/state-save semantics.

- [ ] **Step 2.1: Add RED state/preflight/CLI tests**

```python
def test_monitor_state_round_trips_gemini() -> None:
    state = MonitorState(..., agent="gemini", ...)
    assert MonitorState.model_validate_json(state.model_dump_json()).agent == "gemini"
```

Add Gemini fresh-baseline preflight acceptance, Antigravity/unknown rejection, and CLI guard acceptance tests.
Run: `pytest -q tests/unit/test_release_monitor_state.py tests/unit/test_release_monitor_flow.py -k gemini`
Expected: RED on current literals/guards.
- [ ] **Step 2.2: Add RED flow-semantics tests**

Add Gemini cases for latest/no-new-release, already-qualified, no-downgrade, `--force`, CHECKED persistence, and INCOMPLETE non-persistence. Use fake release source/check executor; do not invoke npm, Docker, or provider.

Run: `pytest -q tests/unit/test_release_monitor_flow.py -k gemini`
Expected: RED until monitor preflight/state support Gemini.

- [ ] **Step 2.3: Implement minimal monitor parity**

Expand only the monitor agent literal/state value. Replace hard-coded Codex/Claude eligibility with Task 1 capability lookup while preserving the existing Antigravity actionable error. Broaden `monitor_command()` supported-agent guard to include Gemini and keep `agent_display_name()` rendering.

- [ ] **Step 2.4: Run GREEN/regressions**

Run:
`pytest -q tests/unit/test_release_monitor_state.py tests/unit/test_release_monitor_flow.py tests/unit/test_release_monitor_cli.py`

If the CLI test filename differs, discover the existing monitor command test module and run it; do not create a redundant CLI suite.

Expected: Gemini cases PASS and all existing Codex/Claude monitor behavior remains green.

- [ ] **Step 2.5: Scoped review and commit**

Review Required TDD items 5–7 plus schema-v1 compatibility. Fix C/I findings before commit.
Commit: `git commit -m "feat: add Gemini release monitor parity"`.

---
### Task 3: Native scheduler parity without credential persistence

**Files:**
- Verify/modify only if needed: `src/qualock/scheduler/commands.py`
- Verify/modify only if needed: `src/qualock/scheduler/runner.py`
- Test: existing scheduler command/state/runner/backend tests under `tests/unit/`

**Interfaces:**
- Consumes: Gemini acceptance in `monitor_preflight()` from Task 2.
- Produces no new schema, backend, or secret-storage interface.
- Preserves: registration fields, native argv, backend definitions, runner PATH restoration.

- [ ] **Step 3.1: Add RED Gemini scheduler eligibility test**

Create a Gemini project/baseline fixture and prove `enable_schedule()` reaches the fake native backend without requiring `GEMINI_API_KEY` at registration time.

```python
outcome = enable_schedule(root, backend=fake_backend, store=fake_store, environ={"PATH": "/bin"})
assert outcome.status is ScheduleStatus.ENABLED
```

Run the focused scheduler command test; expected RED until Task 2 monitor preflight support is present in the integrated tree.

- [ ] **Step 3.2: Add credential non-persistence/runtime-env tests**

Assert serialized registration/native definitions contain neither `GEMINI_API_KEY` nor `QUALOCK_GEMINI_API_KEY`. In runner tests, pass a runtime environment with a sentinel `GEMINI_API_KEY` and assert the monitor child receives it while PATH is replaced by the registered PATH; when absent, assert runner does not fabricate it.
- [ ] **Step 3.3: Keep implementation minimal**

Prefer no scheduler production change beyond what Task 2 unlocks. If a test exposes a real scheduler branch that rejects Gemini, change only that generic eligibility edge; do not add `if agent == "gemini"` to Windows/systemd/launchd backends and do not add credential fields.

- [ ] **Step 3.4: Run cross-backend regressions**

Run the scheduler command, runner, state, Windows, systemd, and launchd unit suites. Confirm generated native definitions remain byte/semantic equivalent for existing agents.

Expected: all scheduler tests PASS; Gemini-specific behavior is inherited from monitor eligibility.

- [ ] **Step 3.5: Scoped review and commit**

Review Required TDD items 8–11, emphasizing “no secret persistence” and absence of Gemini backend branches. If Task 3 required only tests, commit tests only.
Commit: `git commit -m "test: lock Gemini scheduler parity"` or `feat: add Gemini scheduler parity` only if production code actually changes.

---

### Task 4: Forward version bisect parity

**Files:**
- Modify: `src/qualock/version_bisect/models.py`
- Modify: `src/qualock/version_bisect/commands.py`
- Test: `tests/unit/test_version_bisect_commands.py`
- Test: `tests/unit/test_version_bisect_cli.py`
- Test: existing bisect storage tests

**Interfaces:**
- Consumes: Task 1 stable catalog and capability lookup.
- Produces: `BisectAgent = Literal["codex", "claude", "gemini"]`.
- Preserves: summary schema `2`, forward numeric scan and stop semantics.
- [ ] **Step 4.1: Add RED Gemini bisect preflight/CLI tests**

```python
context = bisect_preflight(gemini_root)
assert context.agent_name == "gemini"
assert context.baseline_version == "0.58.0"
```

Add exact-stable upper acceptance, mismatched agent rejection, Antigravity/unknown rejection, and persisted `agent: gemini` assertions.
Run: `pytest -q tests/unit/test_version_bisect_commands.py tests/unit/test_version_bisect_cli.py -k gemini`
Expected: RED on current literal/preflight.

- [ ] **Step 4.2: Add RED forward-scan semantics tests**

Freeze a fake stable catalog such as `("0.58.0", "0.59.0", "0.60.0", "0.61.0")`; assert candidates are `(baseline, upper]` in numeric order. Cover PASS advancement, first BLOCK stop, WARN unresolved stop, INCOMPLETE stop, and save-after-each-step behavior.

- [ ] **Step 4.3: Implement minimal bisect parity**

Expand `BisectAgent`; use Task 1 capability lookup in preflight while retaining domain-specific errors. Reuse `default_stable_release_catalog(context.agent_name)` and existing forward scan unchanged.

- [ ] **Step 4.4: Run GREEN and storage regressions**

Run all bisect command/CLI/storage tests plus `tests/unit/test_agent_releases.py`. Expect schema `2` and legacy summaries unchanged.

- [ ] **Step 4.5: Scoped review and commit**

Review Required TDD items 12–13 and explicit stable-only upper-bound behavior. Fix C/I before commit.
Commit: `git commit -m "feat: add Gemini version bisect parity"`.

---
### Task 5: GitHub PR model, trusted identity, and resolver parity

**Files:**
- Modify: `src/qualock/github_pr/models.py`
- Modify: `src/qualock/github_pr/commands.py`
- Modify: `src/qualock/github_pr/publisher.py`
- Test: `tests/unit/test_github_pr_commands.py`
- Test: existing PR model/report/publisher tests

**Interfaces:**
- Consumes: Task 1 capability lookup, `GeminiResolver`, and `agent_support_fingerprint(AgentBinary) -> str | None`.
- Produces: `PrAgent = Literal["codex", "claude", "gemini"]`.
- Produces: `CandidateRequest(agent_name: PrAgent, version: str, binary_sha256: str, support_sha256: str | None)`.
- Preserves: context/report schema `2`, existing reason codes, Codex/Claude null support compatibility.

- [ ] **Step 5.1: Add RED model/display tests**

Round-trip `PullRequestContext` and `PullRequestReport` with `agent="gemini"`; assert publisher display map renders `Gemini CLI`.
Run focused PR model/publisher tests; expected RED on current `PrAgent` literal/display map.

- [ ] **Step 5.2: Add RED trusted/proposed support identity tests**

Trusted Gemini baseline with missing or malformed `support_sha256` must raise stale-state behavior; trusted Codex/Claude with null support remains valid. Proposed Gemini locks missing/malformed support identity must produce `PrValidationError`; valid 64-hex support reaches resolution.

Run: `pytest -q tests/unit/test_github_pr_commands.py -k 'gemini and support'`
Expected: RED because PR validation currently ignores Gemini support identity.
- [ ] **Step 5.3: Add RED resolved-candidate identity tests**

Use fake resolvers returning Gemini `AgentBinary` values. Assert binary SHA mismatch fails before `check_executor`; missing support fingerprint fails; support mismatch fails; exact binary + support match calls the existing check executor once. Missing credential remains `CREDENTIAL_UNAVAILABLE` with zero resolver/check calls.

- [ ] **Step 5.4: Implement minimal PR identity parity**

Expand `PrAgent`; admit Gemini only when Task 1 says GitHub PR capable. In `_trusted_pr_agent()`, require lowercase 64-hex trusted support SHA only for Gemini. Extend `CandidateRequest`; validate proposed Gemini support SHA. Add `GeminiResolver(default_agent_cache_root())` to `_default_pr_resolver()`. In `qualify_prepared_pr()`, compare resolved entrypoint SHA and `agent_support_fingerprint(resolved)` before invoking qualification.

```python
if candidate.agent_name == "gemini":
    observed_support = agent_support_fingerprint(resolved)
    if observed_support is None or observed_support != candidate.support_sha256:
        raise PrValidationError("resolved Gemini runtime support does not match trusted candidate")
```

- [ ] **Step 5.5: Run GREEN and full PR-command regressions**

Run `pytest -q tests/unit/test_github_pr_commands.py` plus PR model/report/publisher suites. Verify ordinary/mixed-scope PR classification remains unchanged.

- [ ] **Step 5.6: Scoped security review and commit**

Review Required TDD items 14–20, with special attention to trusted-base-only validation and binary+support identity. Fix C/I before commit.
Commit: `git commit -m "feat: add Gemini PR runtime identity"`.

---
### Task 6: GitHub producer workflow, credential transport, setup, and CLI parity

**Files:**
- Modify: `src/qualock/github_pr/templates.py`
- Modify: `src/qualock/cli.py`
- Test: `tests/unit/test_github_pr_templates.py`
- Test: `tests/unit/test_github_pr_setup.py`
- Test: `tests/unit/test_github_pr_cli.py`

**Interfaces:**
- Consumes: `PrAgent` Gemini support from Task 5.
- Produces: producer workflow Gemini branch using repository secret `QUALOCK_GEMINI_API_KEY` mapped only to runtime `GEMINI_API_KEY`.
- Preserves: reporter workflow credential-free, existing pinned action SHAs, permissions, trusted-base checkout, Codex auth materialization/cleanup, Claude precedence.

- [ ] **Step 6.1: Add RED workflow-plan and secret-isolation tests**

Assert plan step accepts `gemini` only for `classification == "upgrade"`; Gemini qualification step is guarded on `agent == 'gemini'`; only that step references `QUALOCK_GEMINI_API_KEY`/`GEMINI_API_KEY`; no credential file is created; secret is absent from reporter workflow, Codex step, Claude step, artifacts, and `GITHUB_OUTPUT` writes.

- [ ] **Step 6.2: Add RED setup/CLI truthfulness tests**

Assert `qualock github setup` output documents all three choices: `QUALOCK_CODEX_AUTH_B64`, Claude's three supported secrets, and `QUALOCK_GEMINI_API_KEY` for trusted Gemini baselines. Preserve statements that setup does not push or configure repository settings.

- [ ] **Step 6.3: Implement minimal template/CLI changes**

Add Gemini to the producer readiness set and one Gemini qualification step. Use `set +x`; derive local `credential_available` from non-empty runtime `GEMINI_API_KEY`; invoke hidden `qualock github qualify-pr`; write no secret to files or outputs. Do not modify reporter permissions or checkout model.
- [ ] **Step 6.4: Run workflow/setup regressions**

Run:
`pytest -q tests/unit/test_github_pr_templates.py tests/unit/test_github_pr_setup.py tests/unit/test_github_pr_cli.py`

Also parse generated YAML using the same parser/assertion strategy already used by the test suite. Confirm action SHAs and permissions are unchanged.

- [ ] **Step 6.5: Scoped security review and commit**

Review Required TDD items 21–25 and fork-safety invariants. Fix C/I before commit.
Commit: `git commit -m "feat: add Gemini PR workflow parity"`.

---

### Task 7: Whole-parity verification, pre-push review, CI-gated delivery docs

**Files:**
- Verify all changed production/test files.
- Modify after exact implementation-head CI only: `README.md`, `ROADMAP.md`, `docs/superpowers/specs/2026-09-10-gemini-orchestration-parity-design.md`.
- Maintain ignored SDD/review ledger under `.superpowers/sdd/2026-09-10-gemini-orchestration-parity/` if used.

**Interfaces:**
- Consumes all Tasks 1–6.
- Produces no new runtime API; establishes merge-ready evidence only.

- [ ] **Step 7.1: Freeze implementation head and run focused parity suites**

Run all Task 1–6 focused suites together, including Codex/Claude regression cases and Antigravity unsupported cases. Record exact head SHA and test counts.

- [ ] **Step 7.2: Run full repository/static gates**

Run canonical full pytest, compileall, `git diff --check`, changed-file Ruff, and strict mypy. Raw strict mypy may contain only the established PyYAML `import-untyped` baseline debt; any new diagnostic is a blocker.
- [ ] **Step 7.3: Prove protected scopes and no schema/dependency drift**

Compare base `45a2250135317601acedea4d15bcb4632586659d` to implementation head. Require zero diff in qualification policy, source materialization, pricing/history behavior, dependency metadata, unrelated runtime adapters, and GitHub reporter permissions. Assert schema literals remain baseline=1, monitor=1, bisect=2, PR=2.

- [ ] **Step 7.4: Fresh whole-implementation review**

Use one fresh high-quality read-only reviewer on exact base→implementation head. Require `SPEC PASS`, `QUALITY APPROVED`, `Critical 0`, `Important 0`. Minors must be triaged explicitly; C/I findings enter scoped fix/re-review loops before any push.

- [ ] **Step 7.5: Hard stop before shared effects**

Do not push or create/update PR until explicit operator authorization. When authorized, push exact frozen implementation head and verify local SHA = remote branch SHA = PR head SHA.

- [ ] **Step 7.6: Hosted CI on exact implementation head**

Require all GitHub Actions jobs, including Windows, green on that exact SHA. Any deterministic CI defect returns to scoped TDD/review; do not mutate delivery docs while implementation-head CI is red.

- [ ] **Step 7.7: Delivery docs only after green implementation CI**

Update README with Gemini monitor/schedule/bisect/PR usage, stable-only discovery, scheduler credential rule, and `QUALOCK_GEMINI_API_KEY`. Move Batch #44 parity item to Delivered in ROADMAP and set the canonical spec status to Delivered. No broader feature claims.

- [ ] **Step 7.8: Docs-inclusive gates and final review**

Repeat required local gates on docs-inclusive head; stop before push until authorized. After docs head CI is green, run one final whole-branch read-only review. Require C0/I0 and truthful docs before requesting merge authorization.

- [ ] **Step 7.9: Merge only on explicit authorization**

Verify PR OPEN/non-draft/MERGEABLE/CLEAN, exact head identity, and all required checks green. Merge only when the operator explicitly authorizes that PR. No tag/release/publish follows automatically.
