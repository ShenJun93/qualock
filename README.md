# Qualock

[![CI](https://github.com/ShenJun93/qualock/actions/workflows/ci.yml/badge.svg)](https://github.com/ShenJun93/qualock/actions/workflows/ci.yml) [![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Lock in known-good coding-agent behavior before you upgrade.**

Qualock is a local-first release-qualification CLI for coding-agent upgrades. It pins a known-good Codex version, reruns repository-specific behavioral canaries against both the baseline and a candidate release in the same qualification window, then emits a CI-friendly `PASS`, `WARN`, `BLOCK`, or `INCOMPLETE` verdict.

> Your unit tests test your code. Qualock tests whether your AI developer still behaves like the version you trust.

## Why

Normal CI validates the code currently in your repository. It does not tell you whether a newly released coding-agent CLI still solves the kinds of tasks your team expects it to solve tomorrow.

Qualock treats the coding agent as a behavioral dependency:

```text
known-good Codex
        vs
candidate Codex release
        ↓
same historical repo SHA
same prepared container image
same task
same hidden grader
paired/interleaved repetitions
        ↓
PASS / WARN / BLOCK / INCOMPLETE
```

## First real qualification evidence

On 2026-09-01, Qualock reran an 18-attempt paired/interleaved pilot comparing Codex `0.150.0` with `0.151.0` using `gpt-5.6-terra` at high reasoning effort after fixing historical Git source isolation.

| Canary | `0.150.0` | `0.151.0` |
| --- | ---: | ---: |
| Starlette URL authority | 3/3 | 3/3 |
| pytest-xdist crash recovery | 3/3 | 3/3 |
| Click sentinel identity | 3/3 | 3/3 |

All 18 attempts were valid and passed the current behavioral graders. Each historical checkout contained exactly its declared base commit, no remotes or refs, and no unreachable Git objects; event-log SHA audit found 0/18 attempts referencing a non-base commit. The clean rerun verdict is `PASS` and supports a **no regression claim** for this small suite.

Candidate total runtime was -16.9% and total input tokens -27.2% versus baseline in this sample; both are advisory in v0.1. See the [clean methodology, per-attempt table, hashes, and isolation provenance](docs/evidence/2026-09-01-codex-0.150.0-vs-0.151.0-clean-rerun/README.md). The original contaminated pilot remains preserved as superseded provenance.

## v0.1 scope

Qualock v0.1 intentionally supports one qualification axis: **Codex CLI version A vs version B**. It does not attempt to be a general agent leaderboard, prompt optimizer, hosted benchmark service, or multi-agent arena.

Core guarantees:

- baseline and candidate are rerun contemporaneously on every `check`;
- attempts are paired/interleaved to reduce run-order bias;
- setup runs once per canary and both sides start from the same prepared image digest;
- hidden graders do not exist in the agent container namespace and are mounted only during the grader phase;
- unknown Codex JSONL events do not crash evidence parsing;
- web/MCP/protected-path contamination can invalidate an attempt;
- three-run critical policy is conservative: only `3/3 -> 0/3` hard-blocks by default;
- token/runtime changes are advisory in v0.1;
- evidence remains local under `.qualock/results/`.

## Install for development

```bash
uv sync --dev
uv run qualock --help
```

Or run directly from the source tree:

```bash
PYTHONPATH=src python -m qualock.cli --help
```

## Quick start

```bash
qualock init
```

Add one or more canary YAML files under `.qualock/canaries/`, each with a pinned historical repository SHA and a private grader patch.

Validate local prerequisites:

```bash
qualock doctor
```

Pin and validate a known-good version:

```bash
qualock baseline codex@0.150.0
```

Then qualify a candidate release:

```bash
qualock check codex@0.151.0
```

The default output is written for the person deciding whether to update:

```text
QuaLock Safety Check

SAFE TO UPDATE

All protected workflows matched the stable baseline in this check.

Protected workflows
- OK: Starlette URL handling  3/3 -> 3/3
- OK: pytest worker recovery  3/3 -> 3/3
- OK: Click sentinel behavior  3/3 -> 3/3

Recommendation:
Codex 0.151.0 looks safe for the workflows you protect.

Technical evidence: .qualock/results/check-.../
```

Use the technical view when you want the raw qualification verdict and counts:

```bash
qualock check codex@0.151.0 --technical
```

Or resolve the current npm release to an exact version before qualification:

```bash
qualock check codex@latest
```

View the latest local report:

```bash
qualock report
```

## Protect a project from AI edits

The easiest path is one command:

```bash
qualock setup
```

QuaLock passively inspects project metadata and recommends built-in checks without executing project code. Project protection requires a Git repository with a committed HEAD; setup validates that before creating `.qualock/`. V1 recognizes Git, Python, pytest, Node/npm, React, Vite, and existing npm scripts named `test`, `build`, `lint`, and `typecheck`.

Example:

```text
QuaLock Setup

Detected: Python, pytest, Git
Protection level: recommended

Recommended protection
- Tests still pass
- Python code still compiles
- Git patch has no whitespace errors

Apply these protections and protect this project? [y/N]:
```

Protection levels are intentionally simple:

- `minimal` chooses one highest-signal available check;
- `recommended` adds normal test/build/compile checks plus Git patch hygiene;
- `strong` also adds detected lint/typecheck scripts.

Use another level or skip the prompt for automation:

```bash
qualock setup --level strong
qualock setup --yes
```

When a project-local `.venv` or `venv` Python exists, generated Python checks use that interpreter. npm checks are generated only for scripts that actually exist in `package.json`. Setup does not install dependencies. Python compile protection is omitted when QuaLock cannot identify a safe source, test, package, or module target.

If every generated check passes, setup delegates to the normal protection engine and creates the same signed `.qualock/project.lock`. If a check fails, the generated config remains so you can fix the project or environment and rerun `qualock protect`, but no known-good lock is created.

For manual control, add or edit local checks in `.qualock/config.yaml`:

```yaml
protections:
  - id: tests
    name: Tests still pass
    command: ["python", "-m", "pytest", "-q"]
    timeout_seconds: 120
```

Record a known-good state:

```bash
qualock protect
```

QuaLock only writes `.qualock/project.lock` when every configured protection passes. The lock freezes the exact protection definitions, so later edits to the config cannot silently weaken verification.

QuaLock also signs that lock. A 32-byte local signing key lives outside the project in QuaLock's user config directory (`~/.config/qualock/project-protection.key` on Linux). If the lock is edited, the key is missing, or the signature no longer matches, `qualock verify` stops before any locked protection command runs and exits with code `4`.

Older unsigned project locks are not silently trusted. First confirm the project is back in a trusted known-good state, then run `qualock protect` again to create a signed lock. Signed project locks are local-machine artifacts by default because verification depends on the user-level key.

This hardening protects against repository-local edits to `.qualock/project.lock`. It does not protect against an agent or process that can also modify QuaLock's user-level signing key.

After your AI changes the project, run:

```bash
qualock verify
```

The default result is intentionally simple:

```text
QuaLock Project Check

SAFE TO KEEP

Protected workflows
- OK: Tests still pass

Recommendation:
The protected behavior is still intact.
```

A regression returns `DON'T KEEP THIS CHANGE` and exit code `2`. A timeout or missing command returns `CHECK COULD NOT FINISH` and exit code `4`. JSON evidence is saved under `.qualock/results/` for both protect and verify runs.

Generated and manual project protections use argv-style commands and execute directly in the project root without a shell. Built-in setup packs are deterministic recommendations, not downloadable marketplace packages. Shell pipes, browser recording, dependency installation, and framework-specific runtime recording are not supported in V1.

## Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | qualification accepted or project protections still pass |
| `1` | operational/internal failure |
| `2` | candidate BLOCKED or a protected project workflow regressed |
| `3` | invalid configuration/input or missing required lock |
| `4` | integrity/incomplete evidence, or project baseline cannot be safely locked |

A `BLOCK` is not a runner crash. It means the qualification ran successfully and the candidate failed policy.

## Canary shape

```yaml
schema_version: 1
id: example-regression
name: Example regression

repository:
  url: https://github.com/example/project.git
  base_sha: 0123456789abcdef0123456789abcdef01234567

runtime:
  image: python:3.12-slim

task: |
  Fix the described behavior while preserving existing behavior.

setup:
  - uv sync --frozen

agent:
  timeout_seconds: 600

grader:
  patch: grader.patch
  command:
    - uv run pytest .qualock-grader/test_regression.py -q

constraints:
  protected_paths:
    - tests/**
    - pyproject.toml
    - uv.lock

critical: true
```

The grader patch is resolved relative to the canary YAML. During the agent phase the grader is not mounted and does not exist in the agent filesystem namespace. After the agent exits, Qualock freezes the agent state and starts a separate grader phase with the grader mounted read-only.

## Behavior lock

`qualock baseline` writes `.qualock/baseline.lock`. The lock stores the known-good exact Codex version and binary SHA-256, model/config fingerprint, suite fingerprint, and historical stability counts.

The stored historical counts are **not** used as the control for future candidates. Every `check` reruns the pinned baseline and candidate in the same qualification window.

## Architecture

```text
Canary
  ↓
Source materializer + prepared Docker image
  ↓
Paired A/B executor
  ↓
Codex JSONL + pre-grader state evidence
  ↓
Hidden grader
  ↓
Deterministic qualification policy
  ↓
Terminal + Markdown + JSON reports
```

The Codex adapter capability-detects each selected binary before constructing the `codex exec` command. The comparison is rejected if a version cannot satisfy the common v0.1 execution/evidence contract.

## Threat model

v0.1 is intended for repositories you trust. Docker is used to create a real hidden-grader filesystem boundary and reproducible prepared state, but Qualock v0.1 is **not** a hardened sandbox for arbitrary hostile repositories.

Qualock copies the Codex auth file to a temporary read-only seed and bootstraps it into an ephemeral `CODEX_HOME` tmpfs for the agent run. Repository code inside that trusted agent container could still access the credential, so hostile repositories remain out of scope. A dedicated credential broker / network egress proxy is future hardening, not a v0.1 claim.

## Development

```bash
python -m pytest -q
ruff check .
mypy src
```

Tests use fake Codex/process/backends so the normal suite does not require OpenAI credentials. Docker-specific integration tests are skipped only when Docker is unavailable.

## Current status

The local qualification pipeline, behavior-lock semantics, paired scheduling, JSONL evidence parsing, hidden-grader isolation, Codex Linux sandbox support, and CLI are implemented. The first real `Codex 0.150.0 -> 0.151.0` OSS pilot has been executed and published. It passed all three canaries after a documented behavioral-grader correction; this is evidence for that small suite only, not a broad equivalence claim.

## Open source and commercial use

The Qualock CLI/core is Apache-2.0 licensed. See [COMMERCIAL.md](COMMERCIAL.md) for the open-core boundary and potential hosted/team features.

## License

Apache-2.0.
