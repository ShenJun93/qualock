from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

import qualock.protocols.first_bad.orchestrate as first_bad_orchestrate
from qualock.baseline.io import write_baseline_lock
from qualock.baseline.models import AgentPin, BaselineLock, ModelPin
from qualock.commands import CommandError
from qualock.config.models import AgentConfig, QualockConfig
from qualock.project import config_fingerprint, load_project, suite_fingerprint
from qualock.protocols.first_bad.orchestrate import (
    FirstBadPreflight,
    _snapshot_project_inputs,
    first_bad_preflight,
)


class FakeCatalog:
    def __init__(self, versions: tuple[str, ...]) -> None:
        self.versions = versions
        self.calls = 0

    def stable_versions(self) -> tuple[str, ...]:
        self.calls += 1
        return self.versions


def baseline_lock(agent: str = "codex", version: str = "0.151.0") -> BaselineLock:
    return BaselineLock(
        schema_version=1,
        created_at="2026-09-02T00:00:00+00:00",
        agent=AgentPin(name=agent, version=version, binary_sha256="a" * 64),
        model=ModelPin(id="gpt-5", snapshot=None, reasoning_effort="medium"),
        qualock_version="0.1.1",
        suite_sha256="b" * 64,
        config_sha256="c" * 64,
        canaries={},
    )


class FakeAgentConfig:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeConfig:
    def __init__(self, agent_name: str) -> None:
        self.agent = FakeAgentConfig(agent_name)


def patch_project_loading(monkeypatch: pytest.MonkeyPatch, *, agent_name: str = "codex") -> None:
    monkeypatch.setattr(
        first_bad_orchestrate, "load_project", lambda root: (FakeConfig(agent_name), [])
    )
    monkeypatch.setattr(first_bad_orchestrate, "suite_fingerprint", lambda canaries: "suite-now")
    monkeypatch.setattr(first_bad_orchestrate, "config_fingerprint", lambda config: "config-now")
    monkeypatch.setattr(first_bad_orchestrate, "assert_suite_fresh", lambda *args: None)


def patch_lock(monkeypatch: pytest.MonkeyPatch, lock: BaselineLock) -> None:
    monkeypatch.setattr(first_bad_orchestrate, "read_baseline_lock", lambda path: lock)


# --- capability / preflight (RED step 1) -----------------------------------


def test_config_lock_agent_mismatch_rejected_before_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="claude")
    patch_lock(monkeypatch, baseline_lock(agent="codex"))
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)
    assert catalog.calls == 0


def test_antigravity_agent_rejected_before_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="antigravity")
    patch_lock(monkeypatch, baseline_lock(agent="antigravity", version="1.0.0"))
    catalog = FakeCatalog(("1.0.0", "1.1.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "antigravity@1.1.0", catalog=catalog)
    assert catalog.calls == 0


def test_unknown_agent_rejected_before_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="other")
    patch_lock(monkeypatch, baseline_lock(agent="other"))
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)
    assert catalog.calls == 0


def test_cross_agent_upper_rejected_before_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="claude")
    patch_lock(monkeypatch, baseline_lock(agent="claude", version="2.1.260"))
    catalog = FakeCatalog(("2.1.261", "2.1.263"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "codex@2.1.261", catalog=catalog)
    assert catalog.calls == 0


@pytest.mark.parametrize("upper", ["codex@latest", "codex@0.153.0-beta.1", "codex@0.153"])
def test_non_exact_non_stable_upper_rejected_before_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upper: str
) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    patch_lock(monkeypatch, baseline_lock(agent="codex", version="0.151.0"))
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, upper, catalog=catalog)
    assert catalog.calls == 0


def test_non_stable_baseline_rejected_before_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    patch_lock(monkeypatch, baseline_lock(agent="codex", version="0.151.0-beta.1"))
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)
    assert catalog.calls == 0


def test_upper_absent_from_catalog_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    patch_lock(monkeypatch, baseline_lock(agent="codex", version="0.151.0"))
    catalog = FakeCatalog(("0.151.0", "0.152.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)


def test_baseline_absent_from_catalog_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    patch_lock(monkeypatch, baseline_lock(agent="codex", version="0.151.0"))
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)


@pytest.mark.parametrize("upper", ["0.151.0", "0.150.0"])
def test_upper_must_be_newer_than_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upper: str
) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    patch_lock(monkeypatch, baseline_lock(agent="codex", version="0.151.0"))
    catalog = FakeCatalog(("0.150.0", "0.151.0", "0.152.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, f"codex@{upper}", catalog=catalog)


def test_malformed_catalog_entry_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    patch_lock(monkeypatch, baseline_lock(agent="codex", version="0.151.0"))
    catalog = FakeCatalog(("0.151.0", "0.152.0", "not-a-version", "0.153.0"))

    with pytest.raises(CommandError):
        first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)


def test_frozen_range_is_sorted_deduplicated_and_fetched_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_project_loading(monkeypatch, agent_name="codex")
    lock = baseline_lock(agent="codex", version="0.151.0")
    patch_lock(monkeypatch, lock)
    catalog = FakeCatalog(
        ("0.153.0", "0.150.0", "0.152.0", "0.151.0", "0.152.0", "0.154.0", "0.153.0")
    )

    result = first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)

    assert catalog.calls == 1
    assert result.catalog == ("0.151.0", "0.152.0", "0.153.0")


@pytest.mark.parametrize("agent_name", ["codex", "claude", "gemini"])
def test_preflight_accepts_supported_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_name: str
) -> None:
    patch_project_loading(monkeypatch, agent_name=agent_name)
    lock = baseline_lock(agent=agent_name, version="1.0.0")
    patch_lock(monkeypatch, lock)
    catalog = FakeCatalog(("1.0.0", "1.1.0", "1.2.0"))

    result = first_bad_preflight(tmp_path, f"{agent_name}@1.2.0", catalog=catalog)

    assert result == FirstBadPreflight(
        agent_name=agent_name,
        baseline_lock=lock,
        upper_version="1.2.0",
        catalog=("1.0.0", "1.1.0", "1.2.0"),
        suite_sha256=lock.suite_sha256,
        config_sha256=lock.config_sha256,
        model_pin=lock.model,
    )


def test_stale_baseline_stops_before_catalog_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qualock.baseline.io import BaselineStaleError

    patch_project_loading(monkeypatch, agent_name="codex")
    monkeypatch.setattr(
        first_bad_orchestrate,
        "assert_suite_fresh",
        lambda *args: (_ for _ in ()).throw(BaselineStaleError("suite changed")),
    )
    patch_lock(monkeypatch, baseline_lock(agent="codex", version="0.151.0"))
    catalog = FakeCatalog(("0.152.0", "0.153.0"))

    with pytest.raises(BaselineStaleError):
        first_bad_preflight(tmp_path, "codex@0.153.0", catalog=catalog)
    assert catalog.calls == 0


# --- real-filesystem preflight: trusted baseline immutability --------------


def _write_config(path: Path, agent: str) -> None:
    config = QualockConfig(agent=AgentConfig(name=agent))
    path.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False), encoding="utf-8")


def _write_real_project(
    root: Path, *, agent: str = "codex", baseline_version: str = "0.151.0"
) -> Path:
    qualock_dir = root / ".qualock"
    qualock_dir.mkdir(parents=True)
    _write_config(qualock_dir / "config.yaml", agent)
    (qualock_dir / "canaries").mkdir()

    config, canaries = load_project(root)
    lock = BaselineLock(
        schema_version=1,
        created_at="2026-09-02T00:00:00+00:00",
        agent=AgentPin(name=agent, version=baseline_version, binary_sha256="a" * 64),
        model=ModelPin(
            id=config.model.effective_model,
            snapshot=None,
            reasoning_effort=config.model.reasoning_effort,
        ),
        qualock_version="0.1.1",
        suite_sha256=suite_fingerprint(canaries),
        config_sha256=config_fingerprint(config),
        canaries={},
    )
    baseline_path = qualock_dir / "baseline.lock"
    write_baseline_lock(baseline_path, lock)
    return baseline_path


def test_real_preflight_leaves_baseline_bytes_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "project"
    baseline_path = _write_real_project(root, agent="codex", baseline_version="0.151.0")
    before = baseline_path.read_bytes()
    catalog = FakeCatalog(("0.150.0", "0.151.0", "0.152.0", "0.153.0"))

    result = first_bad_preflight(root, "codex@0.153.0", catalog=catalog)

    assert baseline_path.read_bytes() == before
    assert result.agent_name == "codex"
    assert result.upper_version == "0.153.0"
    assert result.catalog == ("0.151.0", "0.152.0", "0.153.0")


def test_real_preflight_permits_read_only_real_baseline(tmp_path: Path) -> None:
    root = tmp_path / "project"
    baseline_path = _write_real_project(root, agent="codex", baseline_version="0.151.0")
    before = baseline_path.read_bytes()
    baseline_path.chmod(0o444)
    try:
        catalog = FakeCatalog(("0.151.0", "0.152.0"))
        result = first_bad_preflight(root, "codex@0.152.0", catalog=catalog)
        assert result.upper_version == "0.152.0"
    finally:
        baseline_path.chmod(0o644)
    assert baseline_path.read_bytes() == before


# --- workspace snapshot (RED step 3) ---------------------------------------


def _build_full_project(root: Path) -> Path:
    qualock_dir = root / ".qualock"
    qualock_dir.mkdir(parents=True)
    _write_config(qualock_dir / "config.yaml", "codex")

    canaries_dir = qualock_dir / "canaries"
    canaries_dir.mkdir()
    (canaries_dir / "demo.yaml").write_text("id: demo\n", encoding="utf-8")
    nested = canaries_dir / "patches"
    nested.mkdir()
    (nested / "demo.patch").write_text("--- a\n+++ b\n", encoding="utf-8")

    baseline_path = qualock_dir / "baseline.lock"
    baseline_path.write_text('{"trusted": true}', encoding="utf-8")

    (qualock_dir / "results").mkdir()
    (qualock_dir / "results" / "summary.json").write_text("{}", encoding="utf-8")
    (qualock_dir / "work").mkdir()
    (qualock_dir / "work" / "scratch.txt").write_text("scratch", encoding="utf-8")
    (qualock_dir / "cache").mkdir()
    (qualock_dir / "cache" / "blob.bin").write_bytes(b"\x00\x01")
    (qualock_dir / "sidecar.json").write_text("{}", encoding="utf-8")
    (root / "README.md").write_text("unrelated\n", encoding="utf-8")
    return baseline_path


def test_snapshot_copies_only_config_and_canaries(tmp_path: Path) -> None:
    root = tmp_path / "project"
    workspace = tmp_path / "workspace"
    _build_full_project(root)

    _snapshot_project_inputs(root, workspace)

    ws_qualock = workspace / ".qualock"
    assert (ws_qualock / "config.yaml").read_text(encoding="utf-8") == (
        root / ".qualock" / "config.yaml"
    ).read_text(encoding="utf-8")
    assert (ws_qualock / "canaries" / "demo.yaml").read_text(encoding="utf-8") == "id: demo\n"
    assert (
        ws_qualock / "canaries" / "patches" / "demo.patch"
    ).read_text(encoding="utf-8") == "--- a\n+++ b\n"

    assert not (ws_qualock / "baseline.lock").exists()
    assert not (ws_qualock / "results").exists()
    assert not (ws_qualock / "work").exists()
    assert not (ws_qualock / "cache").exists()
    assert not (ws_qualock / "sidecar.json").exists()
    assert not (workspace / "README.md").exists()


def test_snapshot_rejects_symlinked_config(tmp_path: Path) -> None:
    root = tmp_path / "project"
    workspace = tmp_path / "workspace"
    _build_full_project(root)
    real_config = root / ".qualock" / "config.yaml"
    outside_target = tmp_path / "outside-config.yaml"
    outside_target.write_text(real_config.read_text(encoding="utf-8"), encoding="utf-8")
    real_config.unlink()
    real_config.symlink_to(outside_target)

    with pytest.raises(CommandError):
        _snapshot_project_inputs(root, workspace)


def test_snapshot_rejects_symlinked_canary_file(tmp_path: Path) -> None:
    root = tmp_path / "project"
    workspace = tmp_path / "workspace"
    _build_full_project(root)
    canary_path = root / ".qualock" / "canaries" / "demo.yaml"
    outside_target = tmp_path / "outside-canary.yaml"
    outside_target.write_text(canary_path.read_text(encoding="utf-8"), encoding="utf-8")
    canary_path.unlink()
    canary_path.symlink_to(outside_target)

    with pytest.raises(CommandError):
        _snapshot_project_inputs(root, workspace)


def test_snapshot_rejects_symlinked_canaries_directory(tmp_path: Path) -> None:
    root = tmp_path / "project"
    workspace = tmp_path / "workspace"
    _build_full_project(root)
    canaries_dir = root / ".qualock" / "canaries"
    outside_dir = tmp_path / "outside-canaries"
    shutil.copytree(canaries_dir, outside_dir)
    shutil.rmtree(canaries_dir)
    canaries_dir.symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(CommandError):
        _snapshot_project_inputs(root, workspace)


def test_snapshot_leaves_real_baseline_bytes_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "project"
    workspace = tmp_path / "workspace"
    baseline_path = _build_full_project(root)
    before = baseline_path.read_bytes()

    _snapshot_project_inputs(root, workspace)

    assert baseline_path.read_bytes() == before


def test_snapshot_permits_read_only_real_baseline(tmp_path: Path) -> None:
    root = tmp_path / "project"
    workspace = tmp_path / "workspace"
    baseline_path = _build_full_project(root)
    before = baseline_path.read_bytes()
    baseline_path.chmod(0o444)
    try:
        _snapshot_project_inputs(root, workspace)
    finally:
        baseline_path.chmod(0o644)
    assert baseline_path.read_bytes() == before
