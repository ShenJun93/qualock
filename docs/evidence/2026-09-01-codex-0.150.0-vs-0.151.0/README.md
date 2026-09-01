# First real Qualock Codex A/B qualification

> **SUPERSEDED:** This pilot is preserved for provenance but is not release evidence. Historical source materialization exposed future Git objects. Use the [clean rerun](../2026-09-01-codex-0.150.0-vs-0.151.0-clean-rerun/README.md).

**Result:** `PASS` after correcting an over-constrained hidden grader; no behavioral regression was observed in this three-canary pilot.

- Baseline: Codex `0.150.0`
- Candidate: Codex `0.151.0`
- Model: `gpt-5.6-terra`, reasoning effort `high`
- Schedule: 3 canaries × 2 versions × 3 repetitions = 18 paired/interleaved attempts
- All 18 attempts were valid.

## Corrected result

| Canary | 0.150.0 | 0.151.0 | Baseline median runtime | Candidate median runtime | Baseline median input | Candidate median input |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Starlette URL authority | 3/3 | 3/3 | 115.3s | 136.4s | 268,043 | 209,838 |
| pytest-xdist crash recovery | 3/3 | 3/3 | 96.5s | 99.0s | 261,321 | 312,276 |
| Click sentinel identity | 3/3 | 3/3 | 115.8s | 157.8s | 226,701 | 455,009 |

## Measurement correction

The original run produced `WARN` because the pytest-xdist grader required `_assign_work_unit()` to raise `RuntimeError` for a completed-only unit. Two otherwise valid patches instead skipped the empty unit and shut the worker down without dispatching `send_runtest_some([])`.

That requirement was stricter than the task contract. The grader was corrected to accept either explicit failure or graceful skipping, while still requiring that no empty work is dispatched and the queue is consumed.

- Original xdist score: `2/3 -> 2/3`.
- Historical unmodified xdist base still fails the corrected grader.
- Both previously rejected frozen patches pass the corrected grader.
- All 18 original frozen agent states re-grade `PASS`.
- No agent/model attempt was rerun for this correction.

The original report is preserved and identified by SHA-256 `0d428397d9255ac9972950d640f15e24316ad6b8188edd8d8bfcbaad33c3fc44`.

## Isolation and reproducibility

- Baseline native binary SHA-256: `f0222a59e7d06f7b97014fb672731285b453b945fc0f0aab36c89278dec36e14`.
- Candidate native binary SHA-256: `9739cbc928b9c573be83256acd46668f5dd4f119d2d09e05246895ca2aaf0c9a`.
- Both sides used the same prepared-image digest within each canary.
- Web search and workspace shell network were disabled; Apps, Plugins, and Remote Plugin were disabled on both versions.
- The grader was absent from the agent filesystem namespace and mounted only after agent state was frozen.
- The agent container used `seccomp=unconfined` only to allow Codex inner Bubblewrap user namespaces; Docker `--privileged` and `SYS_ADMIN` were not used.

## Advisory resource signal

Across all nine attempts per side, candidate total runtime was 1355.9s vs 1158.7s baseline (+17.0%).
Candidate total input tokens were 3,093,740 vs 2,518,826 baseline (+22.8%).
Runtime and token usage are advisory only in Qualock v0.1 and do not affect the quality verdict.

## Per-attempt evidence

| Canary | Version | Rep | Valid | Original | Corrected | Runtime | Input tokens | Output tokens | Events SHA | Patch SHA |
| --- | --- | ---: | --- | --- | --- | ---: | ---: | ---: | --- | --- |
| Starlette URL authority | 0.150.0 | 1 | yes | PASS | PASS | 106.8s | 201,022 | 2,843 | `f33257d681c7` | `ffad7f543024` |
| Starlette URL authority | 0.151.0 | 1 | yes | PASS | PASS | 89.6s | 160,668 | 2,065 | `bea5c83ca350` | `ffad7f543024` |
| Starlette URL authority | 0.150.0 | 2 | yes | PASS | PASS | 159.7s | 268,043 | 2,394 | `5cc8ac12451e` | `ffad7f543024` |
| Starlette URL authority | 0.151.0 | 2 | yes | PASS | PASS | 137.7s | 305,372 | 2,784 | `dab6fbdc2c9b` | `ffad7f543024` |
| Starlette URL authority | 0.151.0 | 3 | yes | PASS | PASS | 136.4s | 209,838 | 2,160 | `3cb69b846982` | `ffad7f543024` |
| Starlette URL authority | 0.150.0 | 3 | yes | PASS | PASS | 115.3s | 284,541 | 2,930 | `2021022b23e9` | `ffad7f543024` |
| pytest-xdist crash recovery | 0.150.0 | 1 | yes | PASS | PASS | 96.5s | 261,321 | 2,340 | `c1e4a97fa2d0` | `a276cf4d69d4` |
| pytest-xdist crash recovery | 0.151.0 | 1 | yes | PASS | PASS | 99.0s | 312,276 | 3,190 | `9847c0092c5c` | `9d8e16ad80b3` |
| pytest-xdist crash recovery | 0.150.0 | 2 | yes | PASS | PASS | 72.5s | 221,493 | 1,946 | `42a660b6fb77` | `9d8e16ad80b3` |
| pytest-xdist crash recovery | 0.151.0 | 2 | yes | FAIL | PASS | 422.6s | 775,492 | 5,310 | `ded19beeaef6` | `4bc0c7663a87` |
| pytest-xdist crash recovery | 0.151.0 | 3 | yes | PASS | PASS | 61.4s | 164,928 | 1,966 | `a705b684290f` | `a276cf4d69d4` |
| pytest-xdist crash recovery | 0.150.0 | 3 | yes | FAIL | PASS | 264.1s | 502,784 | 6,710 | `16c943ec0d59` | `50f2ea2c24a1` |
| Click sentinel identity | 0.151.0 | 1 | yes | PASS | PASS | 157.8s | 497,734 | 4,267 | `33858ea0fb34` | `8a5410452082` |
| Click sentinel identity | 0.150.0 | 1 | yes | PASS | PASS | 115.8s | 226,701 | 3,643 | `2947c36c1099` | `ae832dfea8a7` |
| Click sentinel identity | 0.150.0 | 2 | yes | PASS | PASS | 118.9s | 210,660 | 3,893 | `e8d0c7f7a7af` | `05e267d479a8` |
| Click sentinel identity | 0.151.0 | 2 | yes | PASS | PASS | 87.6s | 212,423 | 2,269 | `951fb11305e1` | `2019db8103ac` |
| Click sentinel identity | 0.150.0 | 3 | yes | PASS | PASS | 109.1s | 342,261 | 2,843 | `30af08919d6d` | `2019db8103ac` |
| Click sentinel identity | 0.151.0 | 3 | yes | PASS | PASS | 163.8s | 455,009 | 5,451 | `a79e7c29aa11` | `2019db8103ac` |

Full hashes, frozen-image tags, run order, prepared-image digests, usage fields, repository SHAs, and grader provenance are in [`evidence.json`](evidence.json).

## Interpretation

This pilot found **no quality regression** from Codex `0.150.0` to `0.151.0` on these three canaries after the grader measurement bug was corrected. It is not evidence that the releases are behaviorally equivalent in general; the suite is intentionally small.

A service-side model snapshot was not independently pinnable under ChatGPT authentication. Both versions used the same model ID and reasoning effort in one contemporaneous paired/interleaved qualification window, but model-serving changes remain a possible residual confounder.

## Provenance

- Qualification execution commit: `f07b5f09ed821bf3da78f9cac2d20bf291331a30`.
- Behavioral grader correction commit: `5f69787de783a42b4c660bfa314179543bfd65fc`.
- Original report SHA-256: `0d428397d9255ac9972950d640f15e24316ad6b8188edd8d8bfcbaad33c3fc44`.
