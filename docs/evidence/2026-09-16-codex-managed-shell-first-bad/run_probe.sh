#!/usr/bin/env bash
set -euo pipefail

CASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
QUALOCK_CODEX_CACHE="${QUALOCK_CODEX_CACHE:-$HOME/.cache/qualock/agents/codex}"
CASE_OUTPUT="${CASE_OUTPUT:-${TMPDIR:-/tmp}/qualock-codex-managed-shell-first-bad}"
MOCK_PORT="${MOCK_PORT:-18063}"
CAPTURE_BIND="${CAPTURE_BIND:-127.0.0.1}"
CAPTURE_HOST="${CAPTURE_HOST:-host.docker.internal}"
PROBE_IMAGE="${QUALOCK_PROBE_IMAGE:-alpine:3.23}"
DOCKER_BIN="${DOCKER_BIN:-docker}"
VERSIONS=(0.149.1 0.150.0 0.150.1 0.151.0)

for command_name in python3 "$DOCKER_BIN"; do
  command -v "$command_name" >/dev/null || {
    printf 'missing required command: %s\n' "$command_name" >&2
    exit 2
  }
done

"$DOCKER_BIN" image inspect "$PROBE_IMAGE" >/dev/null 2>&1 || {
  printf 'probe image is not local; refusing to pull: %s\n' "$PROBE_IMAGE" >&2
  exit 2
}

mkdir -p "$CASE_OUTPUT"
REQUESTS_OUT="$CASE_OUTPUT/requests.jsonl"
RUN_LOG="$CASE_OUTPUT/run.log"
SERVER_LOG="$CASE_OUTPUT/server.log"
: > "$RUN_LOG"
python3 - "$CASE_DIR/manifest.json" "$QUALOCK_CODEX_CACHE" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
cache = Path(sys.argv[2])
relative_bin = Path("node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin")
for version, item in manifest["versions"].items():
    base = cache / version / relative_bin
    targets = {
        "codex": (base / "codex", item["codex_sha256"]),
        "support": (base / "codex-code-mode-host", item["support_sha256"]),
    }
    for label, (path, expected) in targets.items():
        if not path.is_file():
            raise SystemExit(f"missing cached {label} binary for {version}: {path}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"{label} hash mismatch for {version}: {actual}")
print("binary provenance preflight: PASS")
PY

EXPECTED_IMAGE_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["container_image_id"])' "$CASE_DIR/manifest.json")"
ACTUAL_IMAGE_ID="$("$DOCKER_BIN" image inspect "$PROBE_IMAGE" --format '{{.Id}}')"
if [[ "$ACTUAL_IMAGE_ID" != "$EXPECTED_IMAGE_ID" ]]; then
  printf 'probe image mismatch: expected %s got %s\n' "$EXPECTED_IMAGE_ID" "$ACTUAL_IMAGE_ID" >&2
  exit 2
fi
python3 "$CASE_DIR/capture_server.py" --out "$REQUESTS_OUT" --bind "$CAPTURE_BIND" --port "$MOCK_PORT" >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!
cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT

python3 - "$CAPTURE_BIND" "$MOCK_PORT" "$SERVER_PID" <<'PY'
import http.client
import os
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
pid = int(sys.argv[3])
for _ in range(50):
    try:
        os.kill(pid, 0)
        connection = http.client.HTTPConnection(host, port, timeout=0.2)
        connection.request("GET", "/health")
        response = connection.getresponse()
        if response.status == 200 and response.read() == b"ok\n":
            break
    except (OSError, http.client.HTTPException):
        time.sleep(0.1)
else:
    raise SystemExit(f"capture server did not become healthy on {host}:{port}")
PY
kill -0 "$SERVER_PID"
if ! "$DOCKER_BIN" run --rm \
  --add-host=host.docker.internal:host-gateway \
  "$PROBE_IMAGE" \
  /bin/busybox wget -qO- "http://${CAPTURE_HOST}:${MOCK_PORT}/health" >/dev/null; then
  printf 'probe container cannot reach loopback capture server via %s:%s\n' "$CAPTURE_HOST" "$MOCK_PORT" >&2
  printf 'for native Linux Docker, set CAPTURE_BIND/CAPTURE_HOST to a host address reachable only from the Docker bridge\n' >&2
  exit 2
fi

for rep in 1 2 3; do
  for version in "${VERSIONS[@]}"; do
    package="$QUALOCK_CODEX_CACHE/$version/node_modules/@openai/codex-linux-x64"
    base_url="http://${CAPTURE_HOST}:${MOCK_PORT}/rep${rep}/${version}/v1"
    printf 'REP=%s VERSION=%s\n' "$rep" "$version" >> "$RUN_LOG"
    set +e
    "$DOCKER_BIN" run --rm \
      --add-host=host.docker.internal:host-gateway \
      --tmpfs /tmp/codexhome \
      -w /tmp \
      -e CODEX_HOME=/tmp/codexhome \
      -e OPENAI_API_KEY=qualock-local-dummy \
      -v "$package:/opt/codex-package:ro" \
      -v "$CASE_DIR/requirements.toml:/etc/codex/requirements.toml:ro" \
      "$PROBE_IMAGE" \
      /opt/codex-package/vendor/x86_64-unknown-linux-musl/bin/codex \
      exec --skip-git-repo-check --ephemeral -m gpt-5.5 \
      -c "model_providers.mock={ name = \"mock\", base_url = \"$base_url\", env_key = \"OPENAI_API_KEY\", wire_api = \"responses\" }" \
      -c 'model_provider="mock"' probe </dev/null >> "$RUN_LOG" 2>&1
    rc=$?
    set -e
    printf 'EXIT=%s\n' "$rc" >> "$RUN_LOG"
    if [[ "$rc" -ne 1 ]]; then
      printf 'unexpected probe exit for %s rep %s: %s\n' "$version" "$rep" "$rc" >&2
      exit 3
    fi
  done
done

cleanup
trap - EXIT
python3 - "$REQUESTS_OUT" <<'PY'
import json
import sys
from pathlib import Path

records = [json.loads(line) for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()]
if len(records) != 12:
    raise SystemExit(f"expected 12 captured requests, got {len(records)}")
observed = {}
for record in records:
    version = record["path"].split("/")[2]
    names = [name for name in record["tools"] if name in {"shell_command", "exec_command"}]
    observed.setdefault(version, []).append(names[0] if names else None)
expected = {
    "0.149.1": ["shell_command"] * 3,
    "0.150.0": [None] * 3,
    "0.150.1": [None] * 3,
    "0.151.0": ["exec_command"] * 3,
}
if observed != expected:
    raise SystemExit(f"tool-registration vector mismatch: {observed!r}")
print("tool-registration vector: PASS")
PY

printf 'reproduction output: %s\n' "$CASE_OUTPUT"
