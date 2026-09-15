from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
BYTE_EXACT_PATHS = (
    "tests/fixtures/first_bad_v1/vectors.json",
    "tests/fixtures/paired_change_v1/no-regression-clean/bundle/baseline.lock",
    "docs/evidence/2026-09-16-codex-managed-shell-first-bad/README.md",
    "docs/evidence/2026-09-16-codex-managed-shell-first-bad/requests.jsonl",
)


def _assert_lf_file(path: Path) -> None:
    assert path.exists(), str(path)
    assert b"\r\n" not in path.read_bytes(), str(path)


def test_byte_exact_artifacts_are_checked_out_with_lf() -> None:
    result = subprocess.run(
        ["git", "check-attr", "eol", "--", *BYTE_EXACT_PATHS],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    observed = {
        line.split(": ", 2)[0]: line.split(": ", 2)[2]
        for line in result.stdout.splitlines()
    }
    assert observed == {path: "lf" for path in BYTE_EXACT_PATHS}
    for rel in BYTE_EXACT_PATHS:
        _assert_lf_file(REPO_ROOT / rel)


def test_lf_guard_rejects_crlf_bytes(tmp_path: Path) -> None:
    fixture = tmp_path / "crlf.txt"
    fixture.write_bytes(b"line one\r\nline two\r\n")
    try:
        _assert_lf_file(fixture)
    except AssertionError:
        pass
    else:
        raise AssertionError("CRLF bytes were accepted")
