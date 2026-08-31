import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from qualock.run.docker import DockerRunner


pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI unavailable")


def docker_ready() -> bool:
    try:
        subprocess.run(["docker", "info"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        return True
    except Exception:
        return False


def test_private_grader_exists_only_in_grader_namespace(tmp_path: Path) -> None:
    if not docker_ready():
        pytest.skip("Docker daemon unavailable")
    sentinel = f"qualock-secret-{uuid.uuid4()}"
    grader = tmp_path / "grader"
    grader.mkdir()
    (grader / "sentinel.txt").write_text(sentinel, encoding="utf-8")
    runner = DockerRunner()

    agent_argv = runner.build_agent_create_argv(
        prepared_image="alpine:3.20",
        container_name=f"ub-isolation-{uuid.uuid4().hex[:8]}",
        agent_root=tmp_path / "empty-agent",
        agent_argv=["/bin/sh", "-lc", f"grep -R {sentinel} /private /workspace 2>/dev/null"],
        environment={},
        replace_agent_binary=False,
    )
    assert str(grader) not in " ".join(agent_argv)
