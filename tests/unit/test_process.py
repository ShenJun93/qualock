import sys
from pathlib import Path

from qualock.run.process import run_process


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
