"""Deterministic local first-bad/v1 lifecycle and portability proof."""
from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

import pytest

import qualock.protocols.first_bad.orchestrate as first_bad_orchestrate
from qualock.protocols.first_bad.models import FirstBadClaimClass
from qualock.protocols.first_bad.orchestrate import FirstBadExecutionDependencies, execute_first_bad
from qualock.protocols.first_bad.verify import verify_first_bad
from tests.integration.test_paired_change_e2e import DeterministicProtocolBackend
from tests.unit.test_first_bad_orchestrate import (
    FakeCatalog,
    _versioned_baseline_executor,
    _versioned_check_executor,
    _write_real_scan_project,
)


def _deps(resolver, backend, *, check_calls: list[str] | None = None):
    check = _versioned_check_executor(resolver, backend)
    if check_calls is not None:
        inner = check
        def check(workspace: Path, spec: str):
            check_calls.append(spec)
            return inner(workspace, spec)
    from qualock.evidence.export import export_evidence_bundle
    return FirstBadExecutionDependencies(
        baseline_executor=_versioned_baseline_executor(resolver, backend),
        check_executor=check,
        evidence_exporter=export_evidence_bundle,
    )


def test_first_bad_e2e_finds_middle_attributable_boundary(tmp_path: Path) -> None:
    root = tmp_path / "project"
    resolver, baseline_path = _write_real_scan_project(root, baseline_version="0.150.0")
    baseline_before = baseline_path.read_bytes()
    backend = DeterministicProtocolBackend(success_versions={"0.150.0", "0.151.0"})
    catalog = FakeCatalog(("0.150.0", "0.151.0", "0.152.0", "0.153.0"))
    check_calls: list[str] = []

    outcome = execute_first_bad(
        root,
        "codex@0.153.0",
        catalog=catalog,
        deps=_deps(resolver, backend, check_calls=check_calls),
    )

    assert check_calls == ["codex@0.151.0", "codex@0.152.0"]
    assert "0.153.0" not in check_calls
    assert outcome.receipt.claim is FirstBadClaimClass.FIRST_ATTRIBUTABLE_BAD
    assert outcome.receipt.boundary_version == "0.152.0"
    assert outcome.receipt.boundary_edge_index == 1
    assert len(outcome.receipt.edges) == 2
    assert verify_first_bad(outcome.package_path) == outcome.receipt
    assert baseline_path.read_bytes() == baseline_before


def test_first_bad_e2e_no_bad_full_range_and_offline_after_project_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    resolver, baseline_path = _write_real_scan_project(root, baseline_version="0.150.0")
    baseline_before = baseline_path.read_bytes()
    versions = ("0.150.0", "0.151.0", "0.152.0", "0.153.0")
    backend = DeterministicProtocolBackend(success_versions=set(versions))
    catalog = FakeCatalog(versions)

    outcome = execute_first_bad(
        root,
        "codex@0.153.0",
        catalog=catalog,
        deps=_deps(resolver, backend),
    )

    assert outcome.receipt.claim is FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND
    assert len(outcome.receipt.edges) == 3
    assert baseline_path.read_bytes() == baseline_before
    assert sorted(path.name for path in outcome.package_path.iterdir()) == [
        "chain-evidence.json", "chain-receipt.json", "edges"
    ]
    assert sorted(path.name for path in (outcome.package_path / "edges").iterdir()) == [
        "000000", "000001", "000002"
    ]

    portable = tmp_path / "portable-first-bad"
    shutil.copytree(outcome.package_path, portable)
    expected = outcome.receipt
    shutil.rmtree(root)

    def poisoned(*args, **kwargs):
        raise AssertionError("offline verifier touched an external execution surface")

    monkeypatch.setattr(first_bad_orchestrate, "first_bad_preflight", poisoned)
    monkeypatch.setattr(first_bad_orchestrate, "execute_check", poisoned)
    monkeypatch.setattr(first_bad_orchestrate, "execute_baseline", poisoned)
    monkeypatch.setattr(subprocess, "run", poisoned)
    monkeypatch.setattr(socket, "create_connection", poisoned)

    assert verify_first_bad(portable) == expected
