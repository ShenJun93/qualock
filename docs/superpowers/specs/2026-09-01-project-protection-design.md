# Project Protection Design

## Goal
Add a project-local safety flow for users who want to know whether an AI edit broke behavior that was working before.

## User flow
1. The user defines friendly project protections in .qualock/config.yaml.
2. qualock protect runs every protection against the current project. It only records a known-good lock when every protection passes.
3. After an AI changes the project, qualock verify reruns the locked protection definitions and compares the new results with the known-good baseline.
4. The default output uses plain language: SAFE TO KEEP, DON'T KEEP THIS CHANGE, or CHECK COULD NOT FINISH.

## Protection definition
Each protection has an id, friendly name, argv-style command, and timeout. Commands execute directly in the project root without a shell. This first slice intentionally excludes browser recording, auto-detection, packs, and hosted execution.

## Lock and evidence
.qualock/project.lock stores the exact protection definitions, creation time, Git HEAD, dirty-state flag, and successful baseline results. Verify uses definitions from the lock, not mutable current config. Protect and verify both write JSON evidence under .qualock/results/.

## Safety semantics
Protect refuses to create a lock if any check fails, times out, or cannot start. Verify returns BLOCK when a previously passing check now exits non-zero, INCOMPLETE when evidence cannot be collected, and PASS only when every locked protection still passes.

Existing Codex qualification behavior, canary policy, Docker isolation, evidence formats, and exit codes remain unchanged.
