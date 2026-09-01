# QuaLock Easy Design

## Goal
Make QuaLock understandable to low-tech and vibe-coder users without weakening the existing regression engine, evidence, or exit-code semantics.

## Product contract
The default `qualock check` output should answer three questions first: Is this update safe for my protected workflows? What changed? What should I do next?

Technical PASS/WARN/BLOCK/INCOMPLETE remains the source of truth. The Easy layer only translates those verdicts into user-facing safety language.

## Safety mapping
- PASS -> SAFE TO UPDATE
- WARN -> REVIEW BEFORE UPDATING
- BLOCK -> DON'T UPDATE YET
- INCOMPLETE -> CHECK COULD NOT FINISH

Each summary contains a headline, one-sentence explanation, recommendation, and one row per protected workflow. Canary display names come from `CanarySpec.name`; technical IDs remain available in technical output and artifacts.

## CLI behavior
`qualock check codex@<version>` prints the Easy safety report by default and preserves the existing exit codes. `--technical` prints the existing technical terminal report instead.

The Easy output must always mention where technical evidence is stored. No web UI, score, percentage, or new policy is introduced in this batch.
