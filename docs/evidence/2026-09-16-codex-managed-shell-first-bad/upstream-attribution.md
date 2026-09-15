# Upstream attribution

## Symptom report

- Upstream issue: openai/codex#41099
- Title: `0.150.1 regression: sessions provisioned without shell tool ...; 0.149.1 works`
- Reported A/B: 0.149.1 has working shell execution; 0.150.1 has neither `shell_command` nor `exec_command`, including `codex exec`, under the same enterprise managed policy.
- This independently corroborates the externally visible symptom and the 0.149.1-good / 0.150.1-bad endpoints.

## Fix

- Upstream PR: openai/codex#41393, merged 2026-08-28T19:07:15Z.
- Title: `Preserve one-shot exec when unified exec is disabled`.
- PR rationale: managed configuration may disable resumable unified execution while leaving shell tools enabled; command execution should remain available.
- Relevant diff removes `!Feature::UnifiedExec` from the early-return shell-tool gate, then registers one-shot `exec_command` when unified exec is disabled.
- `rust-v0.151.0` was published 2026-08-29T09:55:39Z, after the fix merge.

## Claim boundary

Issue #41099 does not establish that 0.150.0 is the first bad release. The local frozen-catalog probe in this packet establishes 0.150.0 as first bad among `[0.149.1, 0.150.0, 0.150.1, 0.151.0]`. The issue corroborates the symptom; PR #41393 corroborates the managed-policy causal mechanism and recovery behavior seen in 0.151.0.
