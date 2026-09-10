# Roadmap

## v0.1 — prove the qualification loop

- Codex version A vs version B qualification loop (the original v0.1 proof).
- Repository-specific hidden canaries.
- Contemporaneous paired/interleaved runs.
- Local PASS/WARN/BLOCK/INCOMPLETE verdicts.
- Native per-user daily release monitoring.
- Publish one reproducible real-world upgrade regression or meaningful behavioral difference.
- Forward version scan for the first confirmed bad stable coding-agent release.
- GitHub pull-request qualification reports.
- Attempt-budgeted local qualification with critical-first canary selection and fail-closed incomplete results.
- Provider-neutral, token-denominated local qualification budgeting for `qualock check`, composable with the attempt-count budget.


## Delivered

- Additional coding-agent adapters: Claude Code and Antigravity local qualification, plus Gemini CLI local `baseline`/`check` with provider-default reasoning, pinned runtime-support identity, API-key automation, and shell-child network isolation (Batch #43).
- Historical canary effectiveness ranking and runtime/token-usage estimates (#41).
- Provider-specific API-equivalent reference cost estimates (#42), advisory only and outside qualification pass/fail policy.

## Commercial layer

Managed runners, private canary vaults, organization policy, audit history, dashboards, SSO, and enterprise support are candidates for a separate hosted/team product. The local CLI remains the open-source adoption wedge.
