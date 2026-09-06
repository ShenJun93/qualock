import os
import sys
import time
from pathlib import Path

import pytest

from qualock.run.process import run_process, run_process_tree

FIXTURE = Path("tests/fixtures/bin/process_fixture.py").resolve()


def test_process_captures_exit_stdout_and_stderr() -> None:
    result = run_process([sys.executable, str(FIXTURE), "exit", "7"], timeout_seconds=5)
    assert result.exit_code == 7
    assert result.stdout.strip() == "hello"
    assert result.stderr.strip() == "oops"
    assert result.timed_out is False


def test_process_uses_explicit_environment() -> None:
    result = run_process(
        [sys.executable, str(FIXTURE), "env"],
        env={"QUALOCK_TEST": "isolated"},
        timeout_seconds=5,
    )
    assert result.stdout.strip() == "isolated"


def test_process_timeout_is_reported() -> None:
    result = run_process(
        [sys.executable, str(FIXTURE), "sleep", "2"],
        timeout_seconds=0.05,
    )
    assert result.timed_out is True
    assert result.exit_code is None


def test_process_can_stream_explicit_stdin() -> None:
    result = run_process(
        [sys.executable, "-c", "import sys; print(sys.stdin.read())"],
        input_text="secret-input",
        timeout_seconds=5,
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "secret-input"
    assert result.stderr == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only: os.getpgrp/os.killpg have no Windows equivalent")
def test_process_tree_timeout_kills_long_lived_descendant(tmp_path: Path) -> None:
    process_info = tmp_path / "process-info"
    script = (
        "import os, pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "pathlib.Path(sys.argv[1]).write_text(f'{os.getpgrp()} {child.pid}'); "
        "time.sleep(60)"
    )

    result = run_process_tree(
        [sys.executable, "-c", script, str(process_info)],
        timeout_seconds=0.2,
    )

    assert result.timed_out is True
    assert result.exit_code is None
    process_group, child_pid = map(int, process_info.read_text(encoding="utf-8").split())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and Path(f"/proc/{child_pid}").exists():
        time.sleep(0.01)
    assert not Path(f"/proc/{child_pid}").exists()
    with pytest.raises(ProcessLookupError):
        os.killpg(process_group, 0)
