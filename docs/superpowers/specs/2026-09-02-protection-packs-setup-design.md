# Protection Packs and Setup Wizard Design

## Goal
Make project protection usable without hand-writing commands. `qualock setup` detects common project capabilities, recommends built-in protection checks, writes only the `protections` section, and immediately records a signed known-good baseline.

## User flow
`qualock setup` works in an existing Git project whether or not `qualock init` was run first. It shows detected capabilities and the proposed friendly checks, asks once before modifying files, writes the selected protections, then calls the existing project-protection engine.

For automation, `--yes` skips confirmation. `--level` accepts `minimal`, `recommended`, or `strong`, defaulting to `recommended`.

## Detection
Detection is filesystem/package-metadata based and never executes project code. V1 recognizes Git, Python, pytest, Node/npm, React, Vite, and npm scripts named `test`, `build`, `lint`, and `typecheck`.

Python is detected from `pyproject.toml`, `setup.py`, `setup.cfg`, or requirements files. Pytest is detected from a `tests/` directory, `pytest.ini`, `conftest.py`, or pytest configuration in `pyproject.toml`. Node metadata comes only from `package.json`; React/Vite are detected from dependencies/devDependencies and scripts.

## Built-in checks
Checks are plain `ProjectProtectionConfig` records, so no second execution engine is introduced.

- Python compile: current Python interpreter runs `-m compileall -q` against discovered source/test directories, falling back to the project root.
- Pytest: current Python interpreter runs `-m pytest -q`.
- npm test/build/lint/typecheck: only recommended when that exact script exists in `package.json`.
- Git patch check: `git diff --check`.

`minimal` selects the single highest-signal available check. `recommended` selects normal tests/build/compile plus Git patch hygiene. `strong` adds available lint/typecheck checks. Duplicate protection ids are removed deterministically.

## Config mutation
Setup may create the normal `.qualock/` directories and default config if absent. When config exists, setup validates it but edits only the raw YAML `protections` key so unrelated fields are retained. It does not alter agent/model/canary qualification settings.

If the user declines confirmation, no file is changed and no protection command runs.

## Protection
After config is written, setup calls the existing `execute_protect`. Therefore all existing behavior remains authoritative: every check must pass, evidence is written under `.qualock/results/`, and `project.lock` is signed with the external user-level key.

If protection fails or is incomplete, setup keeps the generated protection config so the user can fix the project/check environment and rerun `qualock protect`; it does not create a known-good lock.

## Errors and scope
A project with no recognized capability and no Git repository is unsupported and exits as invalid input. Missing required executable during protect remains an incomplete protection result.

V1 does not claim browser recording, shell pipelines, marketplace packs, framework-specific runtime behavior, or dependency installation. Packs are built-in deterministic recommendations over tools already present in the project.

## Compatibility
Qualification fingerprints remain unchanged because `protections` are already excluded from agent qualification fingerprinting. Existing `protect` and `verify` commands, signed-lock semantics, exit codes, and evidence formats remain unchanged.
