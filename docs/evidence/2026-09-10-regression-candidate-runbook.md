# Batch #45 case-study candidate runbook

**Prepared:** 2026-09-11
**Status:** research and no-run execution contract only; no authenticated provider execution has been authorized or performed
**Verifier prerequisite:** merged `main@bdf1d5c1a0a4daafa372c6a988bec798a6deb8a2`

## Decision

Select **Codex CLI `0.149.1` -> `0.150.1`** and the existing critical canary `click-sentinel-duplication` for the first case-study attempt.

The public signal is unusually close to QuaLock's execution path: openai/codex#41099 reports that `0.150.1` sessions, including headless `codex exec`, were provisioned without local shell/file-read tools, while the same account/machine on `0.149.1` retained working shell execution. The report also states that forcing shell-related feature flags did not restore the tool. The reporter used macOS and notes that a server-side provisioning component may interact with the client version, so this is candidate plausibility only, not evidence that Linux QuaLock will reproduce it.

Public sources:

- regression report: https://github.com/openai/codex/issues/41099
- baseline release: https://github.com/openai/codex/releases/tag/rust-v0.149.1
- candidate release: https://github.com/openai/codex/releases/tag/rust-v0.150.1
- npm baseline: https://www.npmjs.com/package/@openai/codex/v/0.149.1
- npm candidate: https://www.npmjs.com/package/@openai/codex/v/0.150.1

Both selected versions are exact stable npm releases, and their Linux x64 package layouts contain the `codex` binary plus `codex-code-mode-host` required by the current QuaLock resolver. No package was installed during this research; package metadata/layout was inspected read-only.
## Ranked candidate screen

The fixed ranking order is: direct mapped evidence, existing deterministic canary fit, narrow attribution/low cost, smallest version gap, then lexical tie-break.

| Rank | Exact stable pair | Public evidence | Disposition |
| --- | --- | --- | --- |
| 1 | Codex `0.149.1 -> 0.150.1` | #41099 A/B reports shell/file tools absent in `0.150.1` headless `codex exec`, restored by `0.149.1` | **Selected.** Directly threatens an existing code-edit canary that requires repository inspection, edits, and tests. |
| 2 | Codex `0.151.0 -> 0.152.0` | #42365 documents the `update_plan` tool becoming default-off in `0.152.0`, citing the official release note/implementing change | Rejected for first run. It is a real deterministic tooling change, but none of QuaLock's existing canaries depends on `update_plan`; adding a special canary merely to surface it would violate the selection preference. |
| 3 | Codex `0.150.1 -> 0.151.0` | #42099 gives an exact A/B regression where `app-server thread/start` stopped persisting zero-turn threads | Rejected. QuaLock uses ephemeral `codex exec`, not app-server thread creation/resume, so the affected surface is outside this qualification path. |

Additional sources:

- `0.152.0` planning-tool behavior: https://github.com/openai/codex/issues/42365
- `0.150.1 -> 0.151.0` app-server regression: https://github.com/openai/codex/issues/42099

A strong older Linux pair, Codex `0.117.0 -> 0.118.0`, was screened because #16407 and #16790 report `apply_patch`/Bubblewrap regressions on Ubuntu/Linux. It is **not an eligible current QuaLock pair**: read-only inspection of the old Linux npm package layout found no `codex-code-mode-host`, which the current resolver requires. Sources: https://github.com/openai/codex/issues/16407 and https://github.com/openai/codex/issues/16790.
Claude Code and Gemini CLI were also screened. Current Claude reports around `2.1.258 -> 2.1.260` are dominated by Desktop/Windows/worktree surfaces; some reports explicitly say the standalone CLI is unaffected, and `2.1.258` is below QuaLock's validated Claude minimum `2.1.260`. Gemini CLI `0.58.0 -> 0.59.0` is stable/eligible, but its published changes center on MCP OAuth SSRF and restricted workspace trust; QuaLock disables MCP/extensions/skills/hooks/subagents for Gemini and sets `GEMINI_SANDBOX=false`, so no direct bundled-canary regression signal was found.

Gemini release source: https://github.com/google-gemini/gemini-cli/releases/tag/v0.59.0
Representative Claude mismatch: https://github.com/anthropics/claude-code/issues/92723

Public issue/release claims above support **selection plausibility only**. They must not be cited later as proof that the selected QuaLock run reproduced a regression.

## Frozen execution contract

- Agent: Codex CLI.
- Baseline: `0.149.1`.
- Candidate: `0.150.1`.
- Model ID: `gpt-5.6-terra`.
- Snapshot: `null`; the provider does not expose an immutable service snapshot in this workflow.
- Reasoning effort: `high`.
- Repetitions: `3` per side.
- Integrity: reject web search, MCP calls, and protected-path changes.
- QuaLock execution commit: `bdf1d5c1a0a4daafa372c6a988bec798a6deb8a2`.
- Canary: `click-sentinel-duplication`, critical, unchanged from the merged repository.
- Canary repository: `https://github.com/pallets/click.git`.
- Historical source SHA: `3cbcf9b11546f4cf10b36d3e2e531733ba6fe001`.

The Click task is selected because it requires repository inspection, an actual source edit, and targeted tests. That makes missing local shell/file tools directly relevant without changing the canary to fit the public report.
## Falsifiable hypothesis and qualifying outcome

Hypothesis: with the same Click source, `gpt-5.6-terra`, reasoning effort `high`, integrity policy, and three repetitions, Codex `0.149.1` will establish a stable `3/3` baseline, while `0.150.1` will produce at least one **valid** failed grader attempt because the public shell/file-tool provisioning regression prevents reliable repository inspection/edit/test work.

The hypothesis is falsified for this experiment if the candidate is `3/3` valid successes. A PASS-only run does **not** close the Batch #45 evidence milestone and does not authorize an automatic fallback candidate.

For this critical canary, the current QuaLock policy maps a stable `3/3` baseline to:

- candidate `0/3` valid successes: `BLOCK`;
- candidate `1/3` or `2/3` valid successes: `WARN`;
- candidate `3/3` valid successes: `PASS`;
- missing/invalid paired attempts: `INCOMPLETE`.

A qualifying case-study result for this candidate therefore requires a stable baseline, a repeated `BLOCK` or `WARN` based on valid attempts, and successful standalone evidence export + verification. `PASS` or `INCOMPLETE` remains useful negative evidence but does not satisfy the roadmap proof item.

The tracked canary bytes are frozen at this execution commit: `click-sentinel.yaml` SHA-256 `b7bbbb17785f17728e55935c164bee0afb1859c8dd9b76506e67ca58d8b22daa`; hidden grader patch SHA-256 `b1c7ada4db81258cf00091e78c35dfa7e51a557dcc3d4967e110e1aef920ff08`.

## Attempt and resource budget

A clean run has exactly **9 provider attempts**: three baseline-lock attempts, then six paired/interleaved check attempts (`3 baseline + 3 candidate`). No extra retries are authorized by this runbook.

Budget basis comes from the clean Codex `0.150.0 -> 0.151.0` Click evidence under the same model/task shape. Its three baseline-side Click attempts used 940,321 input tokens, 9,875 output tokens, and 306.5 seconds of agent runtime; the full six-attempt paired Click check used 1,525,700 input tokens, 18,458 output tokens, and 577.0 seconds. Using the first figure as a proxy for baseline establishment gives a clean-run basis of 2,466,021 input tokens, 28,333 output tokens, and 883.5 seconds of summed agent runtime.

The conservative authorization ceiling is **9 attempts, 4,000,000 observed input tokens, 60,000 observed output tokens, and 30 minutes summed agent runtime**. Before starting the six-attempt check, stop and reassess if the three-attempt baseline phase alone exceeds 1,500,000 input tokens or 10 minutes summed agent runtime.

`qualock check --max-attempts 6` is used as the hard attempt-count cap. `--max-tokens` is intentionally not presented as a hard spend cap here: the current implementation checks that threshold only between complete canaries, so a one-canary experiment cannot rely on it to interrupt the six paired attempts.

As of 2026-09-11, neither exact Codex version is present in the QuaLock agent cache. The resolver will therefore perform a scoped `npm install --prefix ~/.cache/qualock/agents/codex/<version> --no-save @openai/codex@<version>` when each version is first resolved. This is not a global install, but it is still runtime package materialization and is part of the later explicit authorization boundary.
## Exact execution recipe — authorization required

Do not execute this section until the user explicitly authorizes **both** scoped Codex runtime materialization and the nine authenticated provider attempts. The commands deliberately use a second detached worktree at the frozen merged SHA so this research/runbook branch cannot enter the experiment's repository fingerprint.

```bash
set -euo pipefail
export QUALOCK_SHA=bdf1d5c1a0a4daafa372c6a988bec798a6deb8a2
export DISCOVERY=/home/pacmap/qualock-regression-case-study
export EXEC=/home/pacmap/qualock-regression-exec
export PY=/home/pacmap/qualock-easy/.venv/bin/python
export CANARY_ID=click-sentinel-duplication
export CLICK_SHA=3cbcf9b11546f4cf10b36d3e2e531733ba6fe001

test ! -e "$EXEC"
test "$(git -C "$DISCOVERY" merge-base HEAD "$QUALOCK_SHA")" = "$QUALOCK_SHA"
git -C "$DISCOVERY" worktree add --detach "$EXEC" "$QUALOCK_SHA"
cd "$EXEC"
test "$(git rev-parse HEAD)" = "$QUALOCK_SHA"
test -z "$(git status --porcelain)"

PYTHONPATH=src "$PY" -m qualock.cli init
cat > .qualock/config.yaml <<'YAML'
schema_version: 1
agent:
  name: codex
model:
  id: gpt-5.6-terra
  snapshot: null
  reasoning_effort: high
qualification:
  repetitions: 3
integrity:
  reject_web_search: true
  reject_mcp_calls: true
  reject_protected_path_changes: true
canary_globs:
  - benchmarks/oss-smoke/click-sentinel.yaml
YAML
```
The explicit `canary_globs` entry is load-bearing: the experiment consumes the tracked benchmark canary and tracked grader patch directly from the frozen QuaLock commit. Nothing is copied into `.qualock/canaries/`, so the selected task bytes cannot drift from the hashes recorded above.

### Preflight and baseline phase

```bash
export RUNLOG=/home/pacmap/qualock-regression-run-logs
test ! -e "$RUNLOG"
mkdir -m 700 "$RUNLOG"

test "$(sha256sum benchmarks/oss-smoke/click-sentinel.yaml | awk '{print $1}')" = \
  b7bbbb17785f17728e55935c164bee0afb1859c8dd9b76506e67ca58d8b22daa
test "$(sha256sum benchmarks/oss-smoke/graders/click-sentinel.patch | awk '{print $1}')" = \
  b1c7ada4db81258cf00091e78c35dfa7e51a557dcc3d4967e110e1aef920ff08

PYTHONPATH=src "$PY" - <<'PY'
from pathlib import Path
from qualock.project import load_project
config, canaries = load_project(Path.cwd())
assert config.agent.name == "codex"
assert config.model.effective_model == "gpt-5.6-terra"
assert config.model.reasoning_effort == "high"
assert config.qualification.repetitions == 3
assert [c.id for c in canaries] == ["click-sentinel-duplication"]
PY
```

The next command is the first authenticated/provider boundary. It may materialize `@openai/codex@0.149.1` into QuaLock's user cache if absent, then performs exactly three baseline attempts.

```bash
set +e
PYTHONPATH=src "$PY" -m qualock.cli baseline codex@0.149.1 2>&1 | tee "$RUNLOG/baseline.log"
BASELINE_RC=${PIPESTATUS[0]}
set -e

mapfile -t BASELINE_DIRS < <(
  find .qualock/results -mindepth 1 -maxdepth 1 -type d -name 'baseline-*' -print | sort
)
test "${#BASELINE_DIRS[@]}" -eq 1
BASELINE_DIR=${BASELINE_DIRS[0]}
BASELINE_QID=${BASELINE_DIR##*/}

set +e
"$PY" - "$BASELINE_DIR/baseline.json" "$CANARY_ID" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
attempts = payload["canaries"][sys.argv[2]]
if len(attempts) != 3:
    raise SystemExit("baseline attempt count is not exactly 3")
if any(a.get("usage", {}).get("observed") is not True for a in attempts):
    raise SystemExit("baseline usage is not fully observed; stop before candidate check")
input_tokens = sum(a["usage"]["input_tokens"] for a in attempts)
output_tokens = sum(a["usage"]["output_tokens"] for a in attempts)
runtime_ms = sum(a["duration_ms"] for a in attempts)
print(f"baseline: attempts=3 input={input_tokens} output={output_tokens} runtime_ms={runtime_ms}")
if input_tokens > 1_500_000 or runtime_ms > 600_000 or output_tokens > 60_000:
    raise SystemExit(42)
PY
BASELINE_BUDGET_RC=$?
set -e
```

`BASELINE_BUDGET_RC=42` means the baseline phase crossed the pre-check reassessment threshold; any other nonzero value means the accounting contract was not established. In either case do not start the candidate check. The baseline itself must also have exited `0`; exit `4` is an unstable baseline and falsifies the experiment precondition.

```bash
test "$BASELINE_BUDGET_RC" -eq 0
test "$BASELINE_RC" -eq 0

PYTHONPATH=src "$PY" - "$BASELINE_DIR/baseline.json" "$CANARY_ID" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
a = payload["canaries"][sys.argv[2]]
assert len(a) == 3
assert sum(x["valid"] for x in a) == 3
assert sum(x["valid"] and x["success"] for x in a) == 3
PY
```

### Paired candidate check

The six-attempt paired check is the second and final provider-spend phase. `--max-attempts 6` is a hard QuaLock admission cap. Token/runtime telemetry cannot interrupt this single-canary check between its six paired attempts; the operator authorization must explicitly accept that limitation. The three-attempt baseline gate above is therefore the last enforceable spend checkpoint before this atomic paired phase.

```bash
set +e
PYTHONPATH=src "$PY" -m qualock.cli check codex@0.150.1 --max-attempts 6 --technical \
  2>&1 | tee "$RUNLOG/check.log"
CHECK_RC=${PIPESTATUS[0]}
set -e
case "$CHECK_RC" in 0|2|4) ;; *) exit "$CHECK_RC" ;; esac

mapfile -t CHECK_DIRS < <(
  find .qualock/results -mindepth 1 -maxdepth 1 -type d -name 'check-*' -print | sort
)
test "${#CHECK_DIRS[@]}" -eq 1
CHECK_DIR=${CHECK_DIRS[0]}
CHECK_QID=${CHECK_DIR##*/}

test -f "$CHECK_DIR/report.json"
test -f "$CHECK_DIR/qualification.json"
test -f "$CHECK_DIR/evidence-provenance.json"
```

CLI exit `0` covers `PASS` and `WARN`, exit `2` means `BLOCK`, and exit `4` can mean an `INCOMPLETE` result. The artifact assertions above distinguish a real written qualification from a pre-result stale-baseline/input failure that happens to share exit `4`.

### Post-run accounting and source-isolation audit

The post-run ceiling is a claim/accounting gate, not a retroactive kill switch. If the totals exceed the authorization envelope, preserve/export the evidence but perform no additional provider experiment and record the ceiling breach explicitly.

```bash
set +e
"$PY" - "$BASELINE_DIR/baseline.json" "$CHECK_DIR/report.json" "$CANARY_ID" <<'PY'
import json, sys
baseline = json.load(open(sys.argv[1], encoding="utf-8"))["canaries"][sys.argv[3]]
report = json.load(open(sys.argv[2], encoding="utf-8"))
execution = next(x for x in report["executions"] if x["canary_id"] == sys.argv[3])
paired = execution["attempts"]
if len(baseline) != 3 or len(paired) != 6:
    raise SystemExit("unexpected attempt count")
all_attempts = baseline + paired
if any(a.get("usage", {}).get("observed") is not True for a in all_attempts):
    raise SystemExit("usage is not fully observed; exact token ceiling cannot be certified")
input_tokens = sum(a["usage"]["input_tokens"] for a in all_attempts)
output_tokens = sum(a["usage"]["output_tokens"] for a in all_attempts)
runtime_ms = sum(a["duration_ms"] for a in all_attempts)
print(f"total: attempts=9 input={input_tokens} output={output_tokens} runtime_ms={runtime_ms}")
if input_tokens > 4_000_000 or output_tokens > 60_000 or runtime_ms > 1_800_000:
    raise SystemExit(42)
PY
TOTAL_BUDGET_RC=$?
set -e
case "$TOTAL_BUDGET_RC" in
  0) ;;
  42) echo "authorization telemetry ceiling exceeded; preserve evidence, no further provider runs" >&2 ;;
  *) echo "authorization telemetry could not be certified; preserve evidence, no further provider runs" >&2 ;;
esac

mapfile -t SOURCE_DIRS < <(
  find .qualock/work -type d -path "*/$CANARY_ID/source" -print | sort
)
test "${#SOURCE_DIRS[@]}" -eq 2
for src in "${SOURCE_DIRS[@]}"; do
  test "$(git -C "$src" rev-parse HEAD)" = "$CLICK_SHA"
  test -z "$(git -C "$src" branch --show-current)"
  test -z "$(git -C "$src" remote)"
  test -z "$(git -C "$src" status --porcelain)"
  test "$(git -C "$src" rev-list --all --count)" -eq 1
  test -z "$(git -C "$src" show-ref || true)"
  test -f "$src/.git/shallow"
  grep -qx "$CLICK_SHA" "$src/.git/shallow"
  FSCK_OUT=$(git -C "$src" fsck --unreachable --no-reflogs 2>&1)
  test -z "$FSCK_OUT"
done
```

The audit is intentionally against both fresh materializations (`baseline-*` and `check-*`). It requires detached HEAD at the exact historical Click SHA, no remote, no ordinary refs, one reachable commit, a shallow boundary exactly at that SHA, a clean source checkout, and no unreachable objects. This is the same isolation property validated locally before authorization; it prevents future repository history from being available to the agent through Git object traversal.

### Export, standalone verification, and outcome classification

```bash
BUNDLE="/home/pacmap/qualock-regression-bundle-$CHECK_QID"
test ! -e "$BUNDLE"
PYTHONPATH=src "$PY" -m qualock.cli evidence export "$CHECK_QID" --out "$BUNDLE" \
  2>&1 | tee "$RUNLOG/export.log"

"$PY" - "$BUNDLE" > "$RUNLOG/bundle-before.json" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
rows = []
for p in sorted(x for x in root.rglob("*") if x.is_file()):
    st = p.stat()
    rows.append([str(p.relative_to(root)), st.st_size, st.st_mtime_ns,
                 hashlib.sha256(p.read_bytes()).hexdigest()])
print(json.dumps(rows, sort_keys=True))
PY
cd /tmp
PYTHONPATH="$EXEC/src" "$PY" -m qualock.cli evidence verify "$BUNDLE" \
  2>&1 | tee "$RUNLOG/verify.log"
cd "$EXEC"

"$PY" - "$BUNDLE" > "$RUNLOG/bundle-after.json" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
rows = []
for p in sorted(x for x in root.rglob("*") if x.is_file()):
    st = p.stat()
    rows.append([str(p.relative_to(root)), st.st_size, st.st_mtime_ns,
                 hashlib.sha256(p.read_bytes()).hexdigest()])
print(json.dumps(rows, sort_keys=True))
PY
cmp "$RUNLOG/bundle-before.json" "$RUNLOG/bundle-after.json"

"$PY" - "$CHECK_DIR/report.json" "$CANARY_ID" <<'PY'
import json, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
execution = next(x for x in report["executions"] if x["canary_id"] == sys.argv[2])
summary = (report["verdict"], execution["baseline_valid"],
           execution["baseline_successes"], execution["candidate_valid"],
           execution["candidate_successes"])
print("verdict=%s baseline=%s/%s candidate=%s/%s" % summary)
verdict, bv, bs, cv, cs = summary
if bv == 3 and bs == 3 and cv == 3 and cs < 3 and verdict in {"warn", "block"}:
    print("CASE_STUDY=QUALIFYING_REPEATED_REGRESSION")
elif verdict == "pass" and bv == bs == cv == cs == 3:
    print("CASE_STUDY=NONQUALIFYING_PASS_NO_DIFFERENCE")
else:
    print("CASE_STUDY=NONQUALIFYING_OR_REQUIRES_MANUAL_STRUCTURED_REVIEW")
PY
```

`evidence verify` is deliberately invoked from `/tmp`, outside the execution project. The before/after bundle inventory proves the real verification invocation did not change file bytes, sizes, or mtimes. The verifier's no-network/no-process behavior is already an implementation invariant covered by the merged verifier tests; this case-study command does not weaken or replace that proof.

A result is publication-eligible for this selected hypothesis only when the stable baseline is `3/3`, all three candidate attempts are valid, at least one candidate grader attempt fails, QuaLock recomputes `WARN` or `BLOCK`, the source-isolation audit passes, and exported evidence verifies successfully. Public issue #41099 may explain why this candidate was selected, but the final claim must be written solely from these structured QuaLock outcomes.

`PASS 3/3 -> 3/3` is explicit no-difference evidence and does **not** close Batch #45. `INCOMPLETE`, invalid attempts, unknown usage, a failed source-isolation audit, or failed bundle verification also do not close it. None of those outcomes authorizes trying rank 2 or rank 3 automatically.

## Authorization boundary

This runbook itself performs no provider execution. A later authorization must explicitly cover all of the following as one bounded experiment:

- scoped first-use materialization of `@openai/codex@0.149.1` and `@openai/codex@0.150.1` under QuaLock's user cache if they are still absent;
- three authenticated baseline attempts, followed only after the baseline stability/accounting gate by six authenticated paired attempts;
- no retries and no automatic fallback candidate;
- hard cap of nine provider attempts;
- planning envelope of 4,000,000 observed input tokens, 60,000 observed output tokens, and 30 minutes summed agent runtime, with the explicit limitation that the six-attempt single-canary check cannot be interrupted by QuaLock between paired attempts on token/runtime telemetry;
- local source-isolation audit plus evidence export/verify after the provider phases.

If authorization is granted, first write the short candidate-specific execution plan required by Task 7.3, then execute this recipe without changing the frozen agent pair, model/reasoning configuration, canary bytes, repetition count, source SHA, or success criterion. Any change to those inputs, or any second candidate experiment after a no-difference result, requires a new rationale and a new explicit authorization.
