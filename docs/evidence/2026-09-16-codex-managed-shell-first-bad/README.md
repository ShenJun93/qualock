# Codex managed-shell regression case study

This directory publishes a deterministic local reproduction of a Codex CLI command-tool registration regression under frozen managed requirements.

It is not a `first-bad/v1` protocol package. It is a real-release structural case study with a narrower claim: among the frozen catalog `[0.149.1, 0.150.0, 0.150.1, 0.151.0]`, the first observed bad release for this managed-policy precondition is `0.150.0`, and `0.151.0` restores command execution registration.

## Frozen precondition

`requirements.toml` sets:

```toml
[features]
shell_tool = true
unified_exec = false
```

The model is fixed to `gpt-5.5`. The original capture used the exact locally cached Linux x64 release artifacts bound in `provenance.json` and the local `alpine:3.23` image whose image ID is recorded in `manifest.json`.

No authenticated OpenAI/provider request is part of this case study. The Responses endpoint is a local mock that records the outgoing tool list and deliberately returns HTTP 400 after capture.

## Observed vector

- `0.149.1`: `shell_command` — GOOD
- `0.150.0`: no command tool — FIRST_BAD
- `0.150.1`: no command tool — BAD
- `0.151.0`: `exec_command` — FIXED

## Relation to prior QuaLock qualification

The earlier bounded authenticated QuaLock candidate experiment did not reproduce this condition: its baseline/candidate result was `PASS 3/3 -> 3/3`. That does not contradict this packet because this reproduction exercises the managed-policy combination `shell_tool=true` with `unified_exec=false`, which the default qualification path did not establish.

Accordingly, this directory does not claim that the existing default QuaLock qualification caught the regression. It records a product coverage gap and a reproducible release difference.

## Verify the published packet

From this directory:

```bash
sha256sum -c SHA256SUMS
```

`requests.jsonl` is the original 12-request capture. `validation.json` records the derived vector, first-bad boundary, recovery release, provenance gate, and upstream attribution gate. `upstream-attribution.md` separates the external symptom report from the locally established first-bad claim.

## Reproduce locally without provider authentication

Prerequisites are `bash`, `python3`, Docker, the exact four Codex versions already present in the QuaLock user cache, and the exact `alpine:3.23` image already present locally. The runner refuses to install Codex or pull a missing image, and it verifies cached binary hashes before running.

By default the capture server binds only to `127.0.0.1`. Before any Codex probe, the runner requires a Docker container to fetch `/health` through `host.docker.internal`; if that bridge cannot reach the loopback server, the run fails closed. Native Linux Docker users may set `CAPTURE_BIND` and `CAPTURE_HOST` to a host address reachable only from the Docker bridge. `DOCKER_BIN` can override the Docker executable name/path.

```bash
CASE_OUTPUT=/tmp/qualock-codex-managed-shell-first-bad \
MOCK_PORT=18065 \
./run_probe.sh
```
