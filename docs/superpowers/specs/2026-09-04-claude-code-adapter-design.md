# Batch #31 — Claude Code Adapter Design

**Date:** 2026-09-04
**Base:** Batch #30 `feat/agent-agnostic-core` at `24211ab499ed431aa8f9d13752957c2355586a37`

## Goal

Add Claude Code as QuaLock's second local qualification agent while proving the Batch #30 adapter boundary. Users can run `qualock baseline claude@<version>` and `qualock check claude@<version>` when the project config selects Claude and a Claude model. Existing Codex behavior remains unchanged.

## Non-goals

- No Claude release monitor, version bisect, scheduler, or GitHub PR qualification.
- No Claude-specific logic in `run/backend.py`; generic backend/Docker contract changes are allowed only for a genuine cross-agent runtime requirement discovered during implementation. Qualification policy and normalized evidence semantics remain unchanged.
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
- `--restricted`
- `--no-session-persistence`
- `--output-format stream-json`
- `--verbose` (required by Claude Code 2.1.260 for `-p` + `stream-json`)
- `--permission-mode dontAsk`
- `--permission-prompts none`
- `--model <configured model>`
- `--effort <configured reasoning effort>`
- a minimal tool surface: `Bash,Read,Edit,Write`
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
    "allowUnsandboxedCommands": false,
    "enableWeakerNestedSandbox": true
  }
}
```

The adapter does not inherit user CLAUDE.md, hooks, plugins, MCP servers, or settings. `--safe-mode` plus explicit settings/MCP config is the isolation boundary. Sandbox network policy sets `network.deniedDomains: ["*"]` and `strictAllowlist: true`, so Bash and child processes cannot make outbound network connections; this does not block the Claude process itself from reaching the model service.

### Generic runtime dependencies

Claude Code's Linux sandbox requires both `bubblewrap` and `socat`. Batch #31 extends the generic agent contract with immutable `AgentRuntimeDependency(command, apt_package)` data. `CodexAdapter.runtime_dependencies` is empty; `ClaudeAdapter.runtime_dependencies` requests the `socat` package.

`DockerQualificationBackend.prepare` only forwards this generic dependency tuple. `DockerRunner.prepare` requires `bubblewrap` plus any adapter-requested package names and lets the runtime distro resolve its current package candidate. This avoids coupling a floating canary image such as `python:3.12-slim` to Debian-12-only version pins. The resulting prepared-image digest remains part of qualification evidence, so the exact runtime image used for baseline/candidate comparison is preserved in provenance. No Claude name or Claude-specific package appears in the backend or Docker runner. For the default Codex adapter, the generated bootstrap command remains bubblewrap-only.

Because Claude's bubblewrap sandbox runs inside QuaLock's outer Docker container, the explicit Claude settings enable `sandbox.enableWeakerNestedSandbox=true`; this permits the inner sandbox to bind-mount the container's existing `/proc` while the outer Docker container remains the primary process isolation boundary. `failIfUnavailable=true` and `allowUnsandboxedCommands=false` remain mandatory.

## Claude automation credential isolation

Batch #31 does not reuse or copy interactive `/login` state from `~/.claude/.credentials.json`. Real Docker acceptance showed that a credential file which is valid for the host interactive login is not a reliable non-interactive container credential. QuaLock therefore follows Claude Code's documented automation authentication path.

The default Claude backend selects the first non-empty explicit automation credential in documented direct-auth precedence:

1. `ANTHROPIC_AUTH_TOKEN`
2. `ANTHROPIC_API_KEY`
3. `CLAUDE_CODE_OAUTH_TOKEN`

For subscription users, `CLAUDE_CODE_OAUTH_TOKEN` is obtained with `claude setup-token`. If none of these variables is present, local Claude baseline/check fails before Docker execution with a `CommandError` explaining how to configure automation authentication. QuaLock never extracts a token from interactive Claude credential files.

The selected secret value is not placed in Docker `--env KEY=value` metadata, image configuration, command arguments, files, or bind mounts. The invocation carries `(credential_name, credential_value)` as generic in-memory `stdin_secret_env` data. `DockerRunner` creates the container with only the credential variable **name** in its bootstrap argv, starts the container interactively, streams the secret over stdin, exports it into the Claude process environment, clears the bootstrap shell variable, and immediately `exec`s Claude. Docker image/container configuration therefore never receives the secret value.

Explicit Claude settings list all three supported credential variables under `sandbox.credentials.envVars` with `mode: deny`, so sandboxed Bash/tool subprocesses do not inherit the parent Claude process credential. `CLAUDE_CONFIG_DIR=/opt/qualock/claude-home` remains an isolated tmpfs and `DISABLE_AUTOUPDATER=1` prevents the pinned Claude binary from updating itself.

Real contract validation is two-layered. `ClaudeResolver` rejects versions below `2.1.260`, verifies the resolved binary reports the exact requested version, and requires every CLI flag used by the adapter from the binary's actual `--help`. Separately, a real `claude doctor` smoke against the exact adapter-generated settings must report no `Invalid settings`; an intentionally malformed settings file is required to be detected by the same doctor path.

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

Codex continues to use `CodexResolver` and `CodexAdapter`. Claude uses `ClaudeResolver`; the default backend selects an explicit automation credential from the host environment and constructs `ClaudeAdapter(automation_credential=...)`.

`execute_baseline` writes the actual agent name into `AgentPin`. `execute_check` resolves both baseline and candidate with the baseline/candidate agent. Report artifact writing receives the matching display name.

CLI safety/technical rendering derives the display name from the candidate agent instead of hard-coding Codex. Monitor-specific Codex copy remains unchanged because monitor remains Codex-only.

## Config and model behavior

The default config remains:

- `agent.name: codex`
- current GPT model defaults

To use Claude, the project must explicitly set `agent.name: claude` and set `model.id` or `model.snapshot` to a Claude Code-supported model identifier/alias. QuaLock does not guess or rewrite a model identifier.

Existing reasoning effort values `low|medium|high|xhigh` are accepted by the current Claude CLI and require no schema change.

## Error handling

- Invalid/unsupported Claude versions, architectures, version mismatches, or missing required CLI flags raise `ClaudeResolveError`.
- Malformed stream-json or a stream that ends without a final `result` event raises `ClaudeEvidenceError`.
- Agent/config/baseline name mismatches raise `CommandError` before execution.
- Missing Claude automation credentials raise `CommandError` before Docker execution; runtime auth/model failures still surface through existing exit/evidence invalidation paths.
- No credential source path or credential contents appear in report artifacts.

## Security invariants

- Interactive Claude credential files are never read, copied, or mounted for qualification.
- Automation credential values travel only through Docker stdin into the Claude process environment; secret values never appear in Docker argv, `--env`, bind mounts, or image metadata.
- `sandbox.credentials.envVars` denies `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, and `CLAUDE_CODE_OAUTH_TOKEN` to sandboxed commands.
- The settings mount is read-only and `CLAUDE_CONFIG_DIR` is isolated tmpfs.
- No user MCP/plugin/hook/rules config is inherited.
- Claude auto-update is disabled with `DISABLE_AUTOUPDATER=1`.
- Web tools are excluded from the available tool surface.
- Sandboxed Bash network is deny-all via `network.deniedDomains: ["*"]` plus `strictAllowlist: true`.
- MCP configuration is explicit and empty.
- Claude sandbox is enabled with `failIfUnavailable=true`, `allowUnsandboxedCommands=false`, and `enableWeakerNestedSandbox=true` for the outer-Docker/inner-bubblewrap layout.
- Claude prepared runtimes require the distro-provided `socat` package; Codex prepared runtimes do not gain this dependency.
- Existing QuaLock web/MCP/protected-path checks remain authoritative and unchanged.
- Docker frozen-state inspection remains the authority for protected path mutation.
- No secret is placed into Docker `--env KEY=value` metadata or container/image command metadata.

## Tests

Required TDD coverage:

1. Claude resolver exact/latest/cache/architecture/error cases plus minimum validated version, exact `--version`, and required real CLI-flag contract.
2. Claude adapter argv contains `--restricted`, required `--verbose`, isolation, model, effort, minimal tools, MCP, settings, and container path requirements.
3. Claude invocation sets `DISABLE_AUTOUPDATER=1`; sandbox settings deny all Bash network and deny all supported auth env vars to sandboxed commands.
4. Generic Docker stdin-secret transport proves the secret value is absent from create argv, Docker environment metadata, mounts, and image command metadata while the credential name alone is used by the bootstrap shell.
5. Default Claude backend follows direct-auth precedence and fails before Docker when no automation credential exists.
6. Real Claude 2.1.260 contract smoke validates resolved version/flags and `claude doctor` acceptance of the exact generated settings; authenticated model execution is conditional on an explicit automation credential being present.
7. Generic prepare forwards adapter runtime dependencies; Claude requests `socat` while default/Codex prepare stays bubblewrap-only, and prepared-image digest provenance is retained.
8. Claude stream-json parser covers session, Bash, Edit/Write paths, `user/tool_result`, tool errors/exit codes, web, MCP, strict final usage, permission denial, malformed JSON, and unknown events.
9. Config accepts `claude` while default remains `codex`.
10. Baseline/check routing chooses Claude resolver/backend, pins `agent.name="claude"`, rejects mixed agents, and preserves Codex paths.
11. CLI renders `Claude Code` for Claude local checks and preserves exact existing Codex output.
12. `release_monitor`, `version_bisect`, scheduler, and GitHub PR modules remain unchanged and Codex-only.

## Final gate

- full pytest
- focused Claude resolver/adapter/evidence/commands/CLI tests
- Ruff on changed files
- strict mypy on `src/qualock`
- compileall
- `git diff --check`
- scope proof for qualification policy/release-monitor/version-bisect/scheduler/GitHub PR plus proof that backend/Docker changes remain agent-generic
- independent whole-branch review

## Success criterion

A synthetic or real Claude local baseline/check uses the same `DockerQualificationBackend` introduced by Batch #30 without edits to that backend or qualification policy. Codex behavior remains unchanged. This proves QuaLock has moved from a Codex-specific product core to a genuine multi-agent local qualification core.
