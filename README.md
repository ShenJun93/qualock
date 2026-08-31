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

Or resolve the current npm release to an exact version before qualification:

```bash
qualock check codex@latest
```

View the latest local report:

```bash
qualock report
```

## Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | PASS or WARN |
| `1` | operational/internal failure |
| `2` | candidate BLOCKED by qualification policy |
| `3` | invalid configuration/input |
| `4` | integrity/reproducibility failure or INCOMPLETE qualification |

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

Codex authentication may be mounted read-only into the agent container. Repository code should therefore be treated as trusted. A dedicated credential broker / network egress proxy is future hardening, not a v0.1 claim.

## Development

```bash
python -m pytest -q
ruff check .
mypy src
```

Tests use fake Codex/process/backends so the normal suite does not require OpenAI credentials. Docker-specific integration tests are skipped only when Docker is unavailable.

## Current status

The offline control plane, behavior-lock semantics, paired scheduling, JSONL evidence parsing, Docker command/isolation contract, fake qualification pipeline, and CLI are implemented. The first live `Codex 0.150.0 -> 0.151.0` OSS qualification still requires a host with Docker plus working Codex authentication; no live regression claim is made until that experiment is executed and its evidence is published.

## Open source and commercial use

The Qualock CLI/core is Apache-2.0 licensed. See [COMMERCIAL.md](COMMERCIAL.md) for the open-core boundary and potential hosted/team features.

## License

Apache-2.0.
