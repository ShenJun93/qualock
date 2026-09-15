"""CLI contract for offline paired-change claim verification."""

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qualock.cli import app
from qualock.protocols.paired_change.io import (
    PairedChangeVerificationError,
    PairedChangeVerificationReason,
)
from qualock.protocols.paired_change.models import ClaimClass
from tests.unit.test_paired_change_io import _claim_receipt

runner = CliRunner()
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def test_verify_claim_help_exposes_protocol_and_write_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    result = runner.invoke(app, ["evidence", "verify-claim", "--help"])
    assert result.exit_code == 0
    stdout = _strip_ansi(result.stdout)
    assert "--protocol" in stdout
    assert "--write-receipt" in stdout


def test_verify_claim_requires_bundle_and_protocol(tmp_path: Path) -> None:
    result = runner.invoke(app, ["evidence", "verify-claim"])
    assert result.exit_code != 0
    result = runner.invoke(app, ["evidence", "verify-claim", str(tmp_path)])
    assert result.exit_code != 0


def test_verify_claim_read_only_calls_verifier_and_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    protocol = tmp_path / "protocol"
    receipt = _claim_receipt()
    calls: list[tuple[Path, Path]] = []

    def fake_verify(bundle_path: Path, protocol_path: Path):
        calls.append((bundle_path, protocol_path))
        return receipt

    def forbidden_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("read-only verify attempted to write a receipt")

    monkeypatch.setattr("qualock.cli.verify_paired_change", fake_verify)
    monkeypatch.setattr("qualock.cli.write_claim_receipt", forbidden_write)

    result = runner.invoke(
        app, ["evidence", "verify-claim", str(bundle), "--protocol", str(protocol)]
    )
    assert result.exit_code == 0
    assert calls == [(bundle, protocol)]
    assert receipt.qualification_id in result.stdout


def test_verify_claim_unresolved_is_success_and_rendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = _claim_receipt()
    claim = receipt.canary_claims
    from qualock.protocols.paired_change.models import CanaryClaimV1

    unresolved = CanaryClaimV1(canary_id="sample", conditions=(), claim=ClaimClass.UNRESOLVED)
    receipt = receipt.model_copy(update={"canary_claims": (*claim, unresolved)})
    monkeypatch.setattr("qualock.cli.verify_paired_change", lambda b, p: receipt)

    result = runner.invoke(
        app,
        [
            "evidence",
            "verify-claim",
            str(tmp_path / "bundle"),
            "--protocol",
            str(tmp_path / "protocol"),
        ],
    )
    assert result.exit_code == 0
    assert "sample: UNRESOLVED" in result.stdout


def test_verify_claim_write_receipt_verifies_then_writes_create_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = _claim_receipt()
    protocol = tmp_path / "protocol"
    order: list[str] = []

    def fake_verify(bundle_path: Path, protocol_path: Path):
        order.append("verify")
        return receipt

    def fake_write(protocol_path: Path, value):
        order.append("write")
        assert protocol_path == protocol
        assert value == receipt
        return protocol_path / "claim-receipt.json"

    monkeypatch.setattr("qualock.cli.verify_paired_change", fake_verify)
    monkeypatch.setattr("qualock.cli.write_claim_receipt", fake_write)

    result = runner.invoke(
        app,
        [
            "evidence",
            "verify-claim",
            str(tmp_path / "bundle"),
            "--protocol",
            str(protocol),
            "--write-receipt",
        ],
    )
    assert result.exit_code == 0
    assert order == ["verify", "write"]


def test_verify_claim_domain_error_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = PairedChangeVerificationError(
        PairedChangeVerificationReason.CLAIM_MISMATCH, "claim-receipt.json"
    )
    monkeypatch.setattr(
        "qualock.cli.verify_paired_change",
        lambda b, p: (_ for _ in ()).throw(error),
    )
    result = runner.invoke(
        app,
        [
            "evidence",
            "verify-claim",
            str(tmp_path / "bundle"),
            "--protocol",
            str(tmp_path / "protocol"),
        ],
    )
    assert result.exit_code == 3
    assert "claim_mismatch" in result.stdout


def test_verify_claim_unexpected_error_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "qualock.cli.verify_paired_change",
        lambda b, p: (_ for _ in ()).throw(RuntimeError("secret detail")),
    )
    result = runner.invoke(
        app,
        [
            "evidence",
            "verify-claim",
            str(tmp_path / "bundle"),
            "--protocol",
            str(tmp_path / "protocol"),
        ],
    )
    assert result.exit_code == 1
    assert "secret detail" not in result.stdout
    assert "claim verification failed" in result.stdout
