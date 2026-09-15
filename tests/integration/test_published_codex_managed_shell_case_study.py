from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
CASE_STUDY = REPO_ROOT / "docs/evidence/2026-09-16-codex-managed-shell-first-bad"


def test_case_study_records_first_bad_tool_registration_vector() -> None:
    records = [json.loads(line) for line in (CASE_STUDY / "requests.jsonl").read_text().splitlines()]
    assert len(records) == 12

    observed: dict[str, list[str | None]] = {}
    for record in records:
        version = record["path"].split("/")[2]
        command_tools = [name for name in record["tools"] if name in {"shell_command", "exec_command"}]
        observed.setdefault(version, []).append(command_tools[0] if command_tools else None)

    assert observed == {
        "0.149.1": ["shell_command"] * 3,
        "0.150.0": [None] * 3,
        "0.150.1": [None] * 3,
        "0.151.0": ["exec_command"] * 3,
    }


def test_case_study_provenance_binds_release_artifacts() -> None:
    payload = json.loads((CASE_STUDY / "provenance.json").read_text())
    versions = payload["versions"]
    assert [item["version"] for item in versions] == ["0.149.1", "0.150.0", "0.150.1", "0.151.0"]

    for item in versions:
        algorithm, encoded = item["integrity"].split("-", 1)
        assert algorithm == "sha512"
        assert base64.b64decode(encoded).hex() == item["cached_tarball_sha512"]
        assert item["extracted_codex_sha256"] == item["probed_codex_sha256"]
        assert item["extracted_support_sha256"] == item["probed_support_sha256"]
        assert item["codex_match"] is True
        assert item["support_match"] is True


def test_case_study_bundle_is_portable_and_self_checked() -> None:
    required = {
        "README.md", "SHA256SUMS", "capture_server.py", "manifest.json", "provenance.json",
        "requests.jsonl", "requirements.toml", "run_probe.sh", "upstream-attribution.md", "validation.json",
    }
    assert required <= {path.name for path in CASE_STUDY.iterdir() if path.is_file()}

    runner = (CASE_STUDY / "run_probe.sh").read_text()
    server = (CASE_STUDY / "capture_server.py").read_text()
    assert "/home/pacmap" not in runner + server
    assert "QUALOCK_CODEX_CACHE" in runner
    assert "CASE_OUTPUT" in runner

    readme = (CASE_STUDY / "README.md").read_text()
    assert "not a `first-bad/v1` protocol package" in readme
    assert "PASS 3/3 -> 3/3" in readme

    manifest = json.loads((CASE_STUDY / "manifest.json").read_text())
    assert manifest["claim_scope"] == (
        "local deterministic request-shape/tool-registration regression under frozen managed requirements"
    )

    checksum_entries: dict[str, str] = {}
    for line in (CASE_STUDY / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        checksum_entries[name] = digest
    assert set(checksum_entries) == required - {"SHA256SUMS"}
    for name, digest in checksum_entries.items():
        assert hashlib.sha256((CASE_STUDY / name).read_bytes()).hexdigest() == digest

    assert (CASE_STUDY / "run_probe.sh").stat().st_mode & 0o111


def test_portable_runner_materializes_ephemeral_codex_home() -> None:
    runner = (CASE_STUDY / "run_probe.sh").read_text()
    assert "--tmpfs /tmp/codexhome" in runner


def _load_capture_server_module():
    import importlib.util

    path = CASE_STUDY / "capture_server.py"
    spec = importlib.util.spec_from_file_location("qualock_case_capture_server", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capture_server_defaults_to_loopback_and_bounds_requests(tmp_path: Path) -> None:
    module = _load_capture_server_module()
    parser = module.build_parser()
    args = parser.parse_args(["--out", str(tmp_path / "requests.jsonl")])

    assert args.bind == "127.0.0.1"
    assert module.MAX_REQUEST_BYTES <= 1_048_576
    assert module.REQUEST_PATH.fullmatch("/rep1/0.150.0/v1/responses")
    assert not module.REQUEST_PATH.fullmatch("/arbitrary/path")


def test_portable_runner_uses_safe_bind_and_health_readiness() -> None:
    runner = (CASE_STUDY / "run_probe.sh").read_text()
    assert 'CAPTURE_BIND="${CAPTURE_BIND:-127.0.0.1}"' in runner
    assert '--bind "$CAPTURE_BIND"' in runner
    assert '"/health"' in runner
    assert 'kill -0 "$SERVER_PID"' in runner


def test_capture_server_rejects_untrusted_path_and_missing_length(tmp_path: Path) -> None:
    import http.client
    import socket
    import subprocess
    import sys
    import time

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    output = tmp_path / "requests.jsonl"
    process = subprocess.Popen(
        [sys.executable, str(CASE_STUDY / "capture_server.py"), "--out", str(output), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.2)
                connection.request("GET", "/health")
                if connection.getresponse().status == 200:
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("capture server did not become healthy")

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        connection.request("POST", "/arbitrary/path", body=b"{}", headers={"Content-Type": "application/json"})
        assert connection.getresponse().status == 404

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        connection.putrequest("POST", "/rep1/0.150.0/v1/responses")
        connection.endheaders()
        assert connection.getresponse().status == 411
        assert output.read_text(encoding="utf-8") == ""
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_portable_runner_bridges_to_loopback_without_host_network() -> None:
    runner = (CASE_STUDY / "run_probe.sh").read_text()
    assert 'DOCKER_BIN="${DOCKER_BIN:-docker}"' in runner
    assert 'CAPTURE_HOST="${CAPTURE_HOST:-host.docker.internal}"' in runner
    assert '--network "$PROBE_NETWORK"' not in runner
    assert '/bin/busybox wget -qO- "http://${CAPTURE_HOST}:${MOCK_PORT}/health"' in runner
