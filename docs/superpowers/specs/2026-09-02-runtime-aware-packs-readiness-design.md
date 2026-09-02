# Runtime-Aware Protection Packs and Environment Readiness Design

## Goal

Make `qualock setup` distinguish an unhealthy project from an environment that is not ready, while generating protection commands that use the project's intended runtime instead of QuaLock's own Python environment.

This batch extends the existing `project_setup` layer only. It does not add another execution engine: successful setup still writes ordinary `ProjectProtectionConfig` entries and delegates baseline execution and signing to `execute_protect`.

## Scope

V1 adds:

- Python runner detection for uv, Poetry, and local virtual environments;
- Node/npm environment readiness;
- passive framework labels for Django, FastAPI, Next.js, React, Vite, and TypeScript;
- safe framework-specific checks only when a deterministic command exists;
- a readiness result rendered before config mutation or protection execution;
- actionable setup guidance when the environment is not ready.

V1 does not add dependency installation, monorepo traversal, browser checks, shell pipelines, background watch, or automatic verify-after-edit.

## Safety boundary

Project capability detection remains passive: it may read files and parse metadata, but it never imports project modules, executes `setup.py`, runs npm scripts, or evaluates project code.

Readiness probing is a separate phase. It may execute only fixed QuaLock-owned inspection commands for a detected external tool/runtime. A readiness probe must not install dependencies, update lockfiles, synchronize environments, execute package scripts, or import the user's application.

The protection phase remains unchanged: only after readiness is READY and the user accepts the setup does QuaLock write protections and call `execute_protect`, which may run the selected project checks.

## Data model

Extend `ProjectCapabilities` with passive metadata only:

- `python_runner`: `uv`, `poetry`, `venv`, or `none`;
- framework booleans for `django`, `fastapi`, `nextjs`, and `typescript`;
- existing React/Vite/pytest/npm signals remain;
- runner metadata records the intended command prefix, not an arbitrary executable supplied by project config.

Add immutable readiness models:

- `ReadinessStatus`: `ready` or `needs_setup`;
- `ReadinessCheck`: id, friendly name, status, optional detail, optional recommendation;
- `EnvironmentReadiness`: overall status plus ordered checks;
- `SetupPlan` always contains the proposed protections plus readiness; `apply_setup_plan` refuses to mutate or run protections unless readiness is READY. This lets NEEDS SETUP output explain what QuaLock intended to protect without applying it.

Readiness is product guidance, not qualification evidence and not part of agent fingerprints.

Readiness is demand-driven. QuaLock probes only the toolchains required by the proposed protections. A generic Git project must not be blocked because Python or npm is unavailable, and a Node project with no generated npm-script protections must not require `node_modules`.

## Python runner selection

Runner selection is deterministic and metadata-driven:

1. uv when `uv.lock` exists or supported uv project metadata is present;
2. Poetry when `poetry.lock` exists or `[tool.poetry]` metadata is present;
3. project-local `.venv` or `venv` when it has `pyvenv.cfg` and a platform-appropriate Python executable;
4. otherwise `none`.

QuaLock must not fall back to `sys.executable` for generated Python protections. That interpreter belongs to QuaLock, not necessarily the project.

A project-local interpreter is trusted only as an execution target after the user accepts setup. Passive detection must not run it.

Generated Python commands are built from one central runner adapter:

- uv prefix: `uv run --no-sync -- python`;
- Poetry prefix: `poetry run python`;
- local venv prefix: the detected environment Python executable.

Pytest appends `-m pytest -q`; compile protection appends `-m compileall -q <targets>`; Django appends `manage.py check` after the Python executable. Pack code does not hand-build runner variants independently.

## Python readiness

For uv projects:

- require `uv` on PATH;
- resolve the project environment path from `UV_PROJECT_ENVIRONMENT` when explicitly set, otherwise use the documented `.venv` default;
- require the environment path and its platform-appropriate Python executable to exist before any uv command is run;
- only after that passive existence check, use a fixed probe equivalent to `uv run --no-sync python -c <QuaLock-owned constant>`;
- `--no-sync` is mandatory so the probe cannot synchronize the existing environment or update the lockfile;
- an absent environment fails before invoking uv and produces NEEDS SETUP with recommendation `uv sync`;
- a failing fixed probe also produces NEEDS SETUP;
- generated Python protections use `uv run --no-sync -- ...`.

This ordering is deliberate: uv documents that ordinary `uv run` can create a missing project environment. QuaLock therefore never invokes `uv run`, even with `--no-sync`, until an environment already exists.

For Poetry projects:

- require `poetry` on PATH;
- use `poetry env info --executable` as the readiness probe;
- an absent/invalid environment produces NEEDS SETUP with recommendation `poetry install`;
- generated Python protections use `poetry run ...`.

For local venv projects:

- require a valid `pyvenv.cfg` and Python executable;
- readiness checks the executable with a fixed `python -c` command that imports only standard-library modules;
- generated protections use that interpreter path.

If Python protections are recommended but no project runner is ready, setup stops before mutation rather than using QuaLock's Python.

## Node/npm readiness

Node projects remain npm-only in this batch.

Readiness requires:

- `node` and `npm` available on PATH;
- `node_modules` present when an npm test/build/lint/typecheck protection would be generated.

QuaLock does not run `npm install` or `npm ci` during readiness. If dependencies appear absent, setup reports NEEDS SETUP and recommends the user run the project's normal install command.

Generated npm protections continue to use only scripts that actually exist in `package.json`.

## Framework signals and checks

Framework detection stays metadata-based.

- Django: detect declared Django dependency plus `manage.py`; when the Python runner is ready, add `manage.py check` at recommended/strong levels.
- FastAPI: detect declared FastAPI dependency for labeling only. There is no universal FastAPI health command in V1.
- Next.js: detect `next` dependency; use existing npm `build`/`test` scripts only.
- React/Vite: keep metadata labels; use existing scripts only.
- TypeScript: detect `typescript` dependency or `tsconfig.json`; use existing `typecheck` script only, and only at strong level as today.

QuaLock never invents commands from framework conventions when the project does not expose a deterministic check.

## Setup flow

`qualock setup` becomes:

1. passive project detection;
2. committed-Git-HEAD preflight;
3. deterministic pack recommendation;
4. environment readiness probes;
5. render detected stack, readiness, and proposed protections;
6. if readiness is NEEDS SETUP, exit without creating or modifying `.qualock`;
7. otherwise ask for confirmation unless `--yes`;
8. write only the `protections` config key;
9. delegate to existing `execute_protect`;
10. preserve current stale-lock fail-closed behavior if the accepted baseline cannot be locked.

Cancellation remains zero-mutation.

## User-facing states

READY example:

```text
Detected: Python, pytest, uv, FastAPI, Git

Environment
- OK: uv is available
- OK: project Python environment is ready

Recommended protection
- Tests still pass
- Python code still compiles
- Git patch has no whitespace errors
```

NEEDS SETUP example:

```text
Detected: Python, pytest, uv, Git

Environment
- OK: uv is available
- NEEDS SETUP: project Python environment is not ready

QuaLock did not change your project.

Recommended action:
Run: uv sync
Then run: qualock setup
```

A ready environment followed by failing protections remains a project-health failure, not an environment-readiness failure.

## Error and exit semantics

Unsupported project/Git preflight errors keep existing invalid-input behavior.

Environment NEEDS SETUP is not a regression and not a known-good baseline. It exits through the existing incomplete/setup-not-ready path (exit code 4) and performs zero project mutation.

Unexpected readiness-probe execution errors are rendered as NEEDS SETUP with a bounded diagnostic; raw tool output is not treated as evidence that the project itself is broken.

## Compatibility

Existing manual `protect` and `verify` behavior is unchanged.

Existing config schema remains valid. New runner/readiness state is derived at setup time and is not persisted into qualification fingerprints.

Signed project-lock semantics, evidence format, project-protection exit behavior, and agent qualification engine remain unchanged.

## Testing

TDD coverage must include:

- uv/Poetry/local-venv runner precedence;
- no fallback to QuaLock's interpreter;
- uv probes and generated protections always include `--no-sync`;
- missing uv/Poetry/environment -> NEEDS SETUP with zero mutation;
- Node missing `node_modules` -> NEEDS SETUP without install;
- framework labels and Django check generation;
- FastAPI/Next/React/Vite do not invent unsupported commands;
- cancellation and invalid config stay zero-mutation;
- ready environment + failing protection is reported as project failure;
- qualification/run/evidence engine diff remains empty.

## Validated external tool behavior

The design relies on current documented behavior:

- uv documents `.venv` as the default project environment, `UV_PROJECT_ENVIRONMENT` as its override, ordinary `uv run` as creating a missing environment, and `--no-sync` as avoiding synchronization while implying frozen behavior.
- Poetry documents `poetry env info --path` / `--executable` for querying an existing project environment.
- npm installation commands are deliberately excluded from readiness; setup never installs dependencies.

These external commands are implementation details behind readiness adapters and must remain centrally defined and regression-tested.
