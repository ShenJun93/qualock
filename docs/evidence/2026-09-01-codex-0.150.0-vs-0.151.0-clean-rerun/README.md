# Clean rerun: Codex 0.150.0 vs 0.151.0

**Result:** `PASS`. This is the replacement evidence run after historical Git source isolation was fixed.

- Baseline: Codex `0.150.0`
- Candidate: Codex `0.151.0`
- Model: `gpt-5.6-terra`, reasoning effort `high`
- Schedule: 3 canaries × 2 versions × 3 repetitions = 18 paired/interleaved attempts
- All 18 attempts were valid and passed the current behavioral graders.

## Result

| Canary | `0.150.0` | `0.151.0` | Baseline median runtime | Candidate median runtime | Baseline median input | Candidate median input |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Starlette URL authority | 3/3 | 3/3 | 141.8s | 109.0s | 215,895 | 170,847 |
| pytest-xdist crash recovery | 3/3 | 3/3 | 217.3s | 233.7s | 509,502 | 397,668 |
| Click sentinel identity | 3/3 | 3/3 | 102.1s | 94.3s | 318,689 | 166,305 |

## Source isolation audit

The rerun used the source-isolation fix in `d6da463afff65ceb139dcf6ce94aaf766f9abd01`. For every canary checkout:

- `HEAD` exactly matched the declared historical base SHA.
- `.git/shallow` contained only that base SHA.
- zero remotes and zero refs were present.
- `git rev-list --all --count` returned `1`.
- `git fsck --unreachable --no-reflogs` found zero unreachable objects.
- Event-log SHA audit found `0/18` attempts containing a reference to any commit other than the historical base when checked against the full local mirror.

This closes the contamination mode in the superseded pilot, where agents could inspect future Git objects from a full mirror clone.

## Advisory resource signal

Across all nine attempts per side, candidate total runtime was 1278.0s vs 1537.9s baseline (-16.9%).
Candidate total input tokens were 2,323,974 vs 3,193,836 baseline (-27.2%).
Runtime and token usage are advisory only in Qualock v0.1 and do not affect the quality verdict.

## Per-attempt evidence

| Canary | Version | Rep | Valid | Result | Runtime | Input tokens | Output tokens | Events SHA | Patch SHA |
| --- | --- | ---: | --- | --- | ---: | ---: | ---: | --- | --- |
| Starlette URL authority | 0.151.0 | 1 | yes | PASS | 149.1s | 200,888 | 2,610 | `597fb0579366` | `4d0e4779fe84` |
| Starlette URL authority | 0.150.0 | 1 | yes | PASS | 93.1s | 141,166 | 2,301 | `70d505a8b09e` | `4d0e4779fe84` |
| Starlette URL authority | 0.150.0 | 2 | yes | PASS | 171.4s | 223,093 | 2,823 | `28ee9ccf9f6f` | `1af68df67329` |
| Starlette URL authority | 0.151.0 | 2 | yes | PASS | 109.0s | 170,847 | 2,943 | `1d8d7256578f` | `4d0e4779fe84` |
| Starlette URL authority | 0.151.0 | 3 | yes | PASS | 72.5s | 150,083 | 2,583 | `61b689fab9ba` | `4d0e4779fe84` |
| Starlette URL authority | 0.150.0 | 3 | yes | PASS | 141.8s | 215,895 | 2,783 | `d36e6d1befe4` | `4d0e4779fe84` |
| pytest-xdist crash recovery | 0.150.0 | 1 | yes | PASS | 197.9s | 450,642 | 7,344 | `9b00b999906f` | `993a69666515` |
| pytest-xdist crash recovery | 0.151.0 | 1 | yes | PASS | 266.2s | 511,718 | 7,350 | `9e41214ac9d5` | `5800b09c13c7` |
| pytest-xdist crash recovery | 0.150.0 | 2 | yes | PASS | 217.3s | 509,502 | 6,726 | `47cb181c39e0` | `c01dba737f40` |
| pytest-xdist crash recovery | 0.151.0 | 2 | yes | PASS | 233.7s | 397,668 | 6,482 | `a22486b14648` | `ec42b7a251b7` |
| pytest-xdist crash recovery | 0.151.0 | 3 | yes | PASS | 177.1s | 307,391 | 4,071 | `da72a747a3ca` | `ec4a2ca9c08f` |
| pytest-xdist crash recovery | 0.150.0 | 3 | yes | PASS | 409.9s | 713,217 | 4,793 | `2ee9cde06696` | `a53eac43300d` |
| Click sentinel identity | 0.150.0 | 1 | yes | PASS | 102.1s | 184,131 | 3,422 | `b9519f452653` | `76b55e41aba2` |
| Click sentinel identity | 0.151.0 | 1 | yes | PASS | 98.3s | 253,742 | 3,198 | `af1dc713cb49` | `7a5c2d0ce8f0` |
| Click sentinel identity | 0.150.0 | 2 | yes | PASS | 104.1s | 318,689 | 3,658 | `545d078e0d7b` | `0bf975385caf` |
| Click sentinel identity | 0.151.0 | 2 | yes | PASS | 94.3s | 166,305 | 3,219 | `980221ef766c` | `e4ce78c12489` |
| Click sentinel identity | 0.150.0 | 3 | yes | PASS | 100.3s | 437,501 | 2,795 | `7253cb5358ad` | `4a3374dd117f` |
| Click sentinel identity | 0.151.0 | 3 | yes | PASS | 77.9s | 165,332 | 2,166 | `47f19316defc` | `f6d647e50f12` |

Full hashes, frozen-image tags, run order, prepared-image digests, usage fields, repository SHAs, isolation audit data, and superseded-bundle provenance are in [evidence.json](evidence.json).

## Interpretation

This clean rerun found **no quality regression** from Codex `0.150.0` to `0.151.0` on these three canaries. It is not evidence that the releases are behaviorally equivalent in general; the suite is intentionally small.

A service-side model snapshot was not independently pinnable under ChatGPT authentication. Both versions used the same model ID and reasoning effort in one contemporaneous paired/interleaved qualification window, but model-serving changes remain a possible residual confounder.

## Provenance

- Qualification execution commit: `d6da463afff65ceb139dcf6ce94aaf766f9abd01`.
- Source-isolation fix commit: `d6da463afff65ceb139dcf6ce94aaf766f9abd01`.
- Behavioral grader correction commit: `5f69787de783a42b4c660bfa314179543bfd65fc`.
- Source report SHA-256: `e77fdbd8c57b83846db6d46e39487ba3855b78a0e98d68afa3c2fef0669805fd`.
- Superseded evidence bundle SHA-256: `bf69ecea40dc282079ba35386614e7ff74173657a697854e7a90e01a5329f03f`.
- The superseded bundle remains preserved at [the original evidence directory](../2026-09-01-codex-0.150.0-vs-0.151.0/).
