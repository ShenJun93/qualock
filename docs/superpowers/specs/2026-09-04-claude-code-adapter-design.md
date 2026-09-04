# Batch #31 — Claude Code Adapter Design

**Date:** 2026-09-04
**Base:** Batch #30 `feat/agent-agnostic-core` at `24211ab499ed431aa8f9d13752957c2355586a37`

## Goal

Add Claude Code as QuaLock's second local qualification agent while proving the Batch #30 adapter boundary. Users can run `qualock baseline claude@<version>` and `qualock check claude@<version>` when the project config selects Claude and a Claude model. Existing Codex behavior remains unchanged.

## Non-goals

- No Claude release monitor, version bisect, scheduler, or GitHub PR qualification.
- No changes to `run/backend.py`, qualification policy, or normalized evidence semantics unless a genuine contract defect is discovered.
- No automatic migration of existing Codex configs or baselines.
- No implicit mapping from GPT model names to Claude model names.
- No secret-bearing environment variables in Docker container metadata.
- No support for arbitrary non-Linux Claude native binaries in this batch.

## Public contract

`parse_agent_spec` accepts exactly `codex@<version>` and `claude@<version>`. The default generated config stays Codex. `AgentConfig.name` becomes `Literal["codex", "claude"]`.

For baseline/check, the CLI agent name must match `config.agent.name`. Check additionally requires the candidate agent to match the baseline lock agent. Mismatches fail before resolution or execution.

Display names are `Codex` and `Claude Code`. Existing Codex CLI/report text and artifact schema remain byte-compatible.

## Claude resolver

Create `src/qualock/agents/claude_resolver.py`.

The resolver accepts exact semver-like versions plus `latest`. `latest` is resolved through `npm view @anthropic-ai/claude-code version` before caching.

QuaLock installs the native glibc package directly rather than relying on the wrapper package lifecycle:

- x86_64/amd64 -> `@anthropic-ai/claude-code-linux-x64`
- arm64/aarch64 -> `@anthropic-ai/claude-code-linux-arm64`

Cache layout:

`.cache/agents/claude/<version>/node_modules/@anthropic-ai/<platform-package>/claude`

The binary is SHA-256 pinned into `AgentBinary(name="claude", ...)`. Unsupported architectures and malformed npm metadata fail closed. Cached binaries are reused without npm.

This batch targets the existing QuaLock Linux/Docker runtime assumptions. Musl-specific Claude packages are not selected because current prepared canaries already rely on Debian/Ubuntu-style bubblewrap bootstrapping unless bubblewrap is preinstalled.

## Claude invocation adapter

Create `src/qualock/agents/claude.py` implementing the Batch #30 `AgentAdapter` protocol.

The adapter owns all Claude-specific runtime details. It builds a non-interactive invocation using:

- `-p`
- `--safe-mode`
- `--no-session-persistence`
- `--output-format stream-json`
- `--permission-mode dontAsk`
- `--permission-prompts none`
- `--model <configured model>`
- `--effort <configured reasoning effort>`
- a minimal tool surface: `Bash,Read,Edit,Write,Glob,Grep`
- matching `--allowed-tools`
- `--strict-mcp-config`
- `--mcp-config {"mcpServers":{}}`
- `--settings /opt/qualock/claude-settings.json`
- the canary task prompt

The container binary path is `/opt/qualock/claude`.

### Explicit settings

The adapter writes a temporary settings JSON and mounts it read-only. It enables Claude's sandbox and fails closed if the sandbox is unavailable:

```json
{
  "sandbox": {
    "enabled": true,
    "failIfUnavailable": true,
    "autoAllowBashIfSandboxed": true,
    "allowUnsandboxedCommands": false
  }
}
```

The adapter does not inherit user CLAUDE.md, hooks, plugins, MCP servers, or settings. `--safe-mode` plus explicit settings/MCP config is the isolation boundary.

## Claude credential isolation

The default host credential source is `~/.claude/.credentials.json` when present.

The source credential file is never mounted directly. The adapter copies it into a temporary host file, mounts that copy read-only at `/opt/qualock/claude-credentials-seed.json`, mounts `/opt/qualock/claude-home` as tmpfs, and requests bootstrap copy to `/opt/qualock/claude-home/.credentials.json` with the existing restrictive bootstrap mechanism.

`CLAUDE_CONFIG_DIR=/opt/qualock/claude-home` and `DISABLE_AUTOUPDATER=1` are non-secret and may be passed as environment variables. Disabling the Claude auto-updater is required so an exact resolved binary cannot mutate its runtime version. The temporary credential seed and settings file must remain alive until Docker start/attach completes and must be deleted when the invocation context exits.

If no host credential file exists, the adapter still creates the isolated config tmpfs and settings but does not mount a credential seed. Claude then fails authentication normally; QuaLock does not smuggle host API keys into container environment metadata in this batch.

## Claude stream-json evidence

Create `src/qualock/evidence/claude_stream_json.py` with `ClaudeEvidenceError(AgentEvidenceError)` and `parse_claude_stream_json(lines) -> AgentEvidence`.

The parser is tolerant of additive event fields but strict about malformed JSON/non-object lines.

Normalization:

- system/init `session_id` -> `thread_id`
- assistant `tool_use` named `Bash` -> `CommandEvent(command=...)`
- assistant `tool_use` named `Write`/`Edit` -> `file_changes` from `file_path` or `path` when present
- `WebSearch`/`WebFetch` tool use -> `web_searches`
- tool names starting `mcp__` -> `mcp_calls`
- final `result.usage.input_tokens` -> `input_tokens`
- final `result.usage.cache_read_input_tokens` -> `cached_input_tokens`
- final `result.usage.output_tokens` -> `output_tokens`
- non-success result subtype, `is_error=true`, or non-empty `permission_denials` -> `errors`
- unknown top-level event types -> `unknown_events`

Usage is read from final result only to avoid double-counting assistant-message usage. `reasoning_output_tokens` remains zero because Claude stream output does not expose an equivalent field required by QuaLock.

## Routing

Add small agent-routing helpers in `commands.py` (or a focused routing module if needed):

- resolver factory by agent name
- adapter/backend factory by agent name
- display-name mapping

Codex continues to use `CodexResolver` and `CodexAdapter`. Claude uses `ClaudeResolver` and `ClaudeAdapter(auth_home=~/.claude if it exists else None)`.

`execute_baseline` writes the actual agent name into `AgentPin`. `execute_check` resolves both baseline and candidate with the baseline/candidate agent. Report artifact writing receives the matching display name.

CLI safety/technical rendering derives the display name from the candidate agent instead of hard-coding Codex. Monitor-specific Codex copy remains unchanged because monitor remains Codex-only.

## Config and model behavior

The default config remains:

- `agent.name: codex`
- current GPT model defaults

To use Claude, the project must explicitly set `agent.name: claude` and set `model.id` or `model.snapshot` to a Claude Code-supported model identifier/alias. QuaLock does not guess or rewrite a model identifier.

Existing reasoning effort values `low|medium|high|xhigh` are accepted by the current Claude CLI and require no schema change.

## Error handling

- Invalid/unsupported Claude versions or architectures raise `ClaudeResolveError`.
- Malformed stream-json raises `ClaudeEvidenceError`.
- Agent/config/baseline name mismatches raise `CommandError` before execution.
- Claude auth/model/runtime failures surface through the existing agent exit/evidence invalidation paths.
- No credential source path or credential contents appear in report artifacts.

## Security invariants

- No original Claude credential file is mounted directly.
- Credential destination is tmpfs and copied under restrictive bootstrap permissions.
- Settings and seed mounts are read-only.
- No user MCP/plugin/hook/rules config is inherited.
- Claude auto-update is disabled with `DISABLE_AUTOUPDATER=1`.
- Web tools are excluded from the available tool surface.
- MCP configuration is explicit and empty.
- Claude sandbox is enabled with `failIfUnavailable=true`.
- Existing QuaLock web/MCP/protected-path checks remain authoritative and unchanged.
- Docker frozen-state inspection remains the authority for protected path mutation.
- No secret is placed into Docker `--env KEY=value` metadata.

## Tests

Required TDD coverage:

1. Claude resolver exact/latest/cache/architecture/error cases.
2. Claude adapter argv contains isolation, model, effort, tool, MCP, settings, and container path requirements.
3. Claude invocation sets `DISABLE_AUTOUPDATER=1`; the credential seed is a temporary copy, mounted read-only, bootstrapped into tmpfs, and deleted after context exit.
4. Missing credential file yields isolated config/settings without a secret mount.
5. Claude stream-json parser covers session, Bash, Edit/Write paths, web, MCP, final usage, permission denial, result errors, malformed JSON, and unknown events.
6. Config accepts `claude` while default remains `codex`.
7. Baseline/check routing chooses Claude resolver/backend, pins `agent.name="claude"`, rejects mixed agents, and preserves Codex paths.
8. CLI renders `Claude Code` for Claude local checks and preserves exact existing Codex output.
9. `release_monitor`, `version_bisect`, scheduler, and GitHub PR modules remain unchanged and Codex-only.

## Final gate

- full pytest
- focused Claude resolver/adapter/evidence/commands/CLI tests
- Ruff on changed files
- strict mypy on `src/qualock`
- compileall
- `git diff --check`
- scope proof for release-monitor/version-bisect/scheduler/GitHub PR
- independent whole-branch review

## Success criterion

A synthetic or real Claude local baseline/check uses the same `DockerQualificationBackend` introduced by Batch #30 without edits to that backend or qualification policy. Codex behavior remains unchanged. This proves QuaLock has moved from a Codex-specific product core to a genuine multi-agent local qualification core.
