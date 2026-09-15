"""CLI contract for offline first-bad/v1 chain verification."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from qualock.cli import app
from qualock.protocols.first_bad.io import (
    FirstBadVerificationError,
    FirstBadVerificationReason,
)
from qualock.protocols.first_bad.models import (
    EdgeClassification,
    FirstBadCanarySummaryV1,
    FirstBadClaimClass,
    FirstBadEdgeSummaryV1,
    FirstBadReceiptV1,
)
from qualock.protocols.first_bad.render import render_first_bad_receipt
from qualock.protocols.paired_change.models import ClaimClass

runner = CliRunner()

SHA = "0" * 64


def _edge(
    index: int,
    baseline: str,
    candidate: str,
    classification: EdgeClassification,
) -> FirstBadEdgeSummaryV1:
    return FirstBadEdgeSummaryV1(
        index=index,
        baseline_version=baseline,
        candidate_version=candidate,
        canaries=(FirstBadCanarySummaryV1(canary_id="sample", claim=ClaimClass.NO_REGRESSION_OBSERVED),),
        classification=classification,
    )


def _receipt(
    *,
    edges: tuple[FirstBadEdgeSummaryV1, ...],
    claim: FirstBadClaimClass,
    boundary_version: str | None = None,
    boundary_edge_index: int | None = None,
) -> FirstBadReceiptV1:
    return FirstBadReceiptV1(
        schema_version=1,
        protocol_id="first-bad/v1",
        chain_sha256=SHA,
        catalog_sha256=SHA,
        conditions=(),
        edges=edges,
        claim=claim,
        boundary_version=boundary_version,
        boundary_edge_index=boundary_edge_index,
    )


def _no_bad_receipt() -> FirstBadReceiptV1:
    edges = (
        _edge(0, "0.150.0", "0.151.0", EdgeClassification.NO_REGRESSION_OBSERVED),
        _edge(1, "0.151.0", "0.152.0", EdgeClassification.NO_REGRESSION_OBSERVED),
    )
    return _receipt(edges=edges, claim=FirstBadClaimClass.NO_ATTRIBUTABLE_BAD_FOUND)


def _first_bad_receipt() -> FirstBadReceiptV1:
    edges = (
        _edge(0, "0.150.0", "0.151.0", EdgeClassification.NO_REGRESSION_OBSERVED),
        _edge(1, "0.151.0", "0.152.0", EdgeClassification.ATTRIBUTABLE_CHANGESET),
    )
    return _receipt(
        edges=edges,
        claim=FirstBadClaimClass.FIRST_ATTRIBUTABLE_BAD,
        boundary_version="0.152.0",
        boundary_edge_index=1,
    )


def _unresolved_receipt() -> FirstBadReceiptV1:
    edges = (_edge(0, "0.150.0", "0.151.0", EdgeClassification.UNRESOLVED),)
    return _receipt(edges=edges, claim=FirstBadClaimClass.UNRESOLVED)


# --- renderer -----------------------------------------------------------------------


def test_render_no_bad_receipt_core_text() -> None:
    text = render_first_bad_receipt(_no_bad_receipt())
    assert text == (
        "QuaLock First-Bad Verification\n"
        "Range: 0.150.0 -> 0.152.0\n"
        "0.150.0 -> 0.151.0  NO REGRESSION\n"
        "0.151.0 -> 0.152.0  NO REGRESSION\n"
        "NO ATTRIBUTABLE BAD FOUND\n"
    )


def test_render_first_attributable_bad_core_text() -> None:
    text = render_first_bad_receipt(_first_bad_receipt())
    assert text == (
        "QuaLock First-Bad Verification\n"
        "Range: 0.150.0 -> 0.152.0\n"
        "0.150.0 -> 0.151.0  NO REGRESSION\n"
        "0.151.0 -> 0.152.0  ATTRIBUTABLE CHANGE\n"
        "FIRST ATTRIBUTABLE BAD: 0.152.0\n"
    )


def test_render_unresolved_core_text() -> None:
    text = render_first_bad_receipt(_unresolved_receipt())
    assert text == (
        "QuaLock First-Bad Verification\n"
        "Range: 0.150.0 -> 0.151.0\n"
        "0.150.0 -> 0.151.0  UNRESOLVED\n"
        "UNRESOLVED\n"
    )


def test_render_does_not_leak_raw_json_or_child_event_content() -> None:
    text = render_first_bad_receipt(_first_bad_receipt())
    assert "{" not in text
    assert "canary_id" not in text
    assert "sample" not in text
    assert SHA not in text


# --- CLI --------------------------------------------------------------------------


def test_verify_first_bad_help_exposes_write_flag() -> None:
    result = runner.invoke(app, ["evidence", "verify-first-bad", "--help"])
    assert result.exit_code == 0
    assert "--write-receipt" in result.stdout
    assert "--protocol" not in result.stdout


def test_verify_first_bad_requires_chain_dir() -> None:
    result = runner.invoke(app, ["evidence", "verify-first-bad"])
    assert result.exit_code != 0


def test_verify_first_bad_no_bad_exits_zero_and_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = tmp_path / "chain"
    receipt = _no_bad_receipt()
    calls: list[Path] = []

    def fake_verify(chain_path: Path):
        calls.append(chain_path)
        return receipt

    def forbidden_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("read-only verify attempted to write a receipt")

    monkeypatch.setattr("qualock.cli.verify_first_bad", fake_verify)
    monkeypatch.setattr("qualock.cli.write_first_bad_receipt", forbidden_write)

    result = runner.invoke(app, ["evidence", "verify-first-bad", str(chain)])
    assert result.exit_code == 0
    assert calls == [chain]
    assert "NO ATTRIBUTABLE BAD FOUND" in result.stdout


def test_verify_first_bad_first_attributable_bad_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("qualock.cli.verify_first_bad", lambda chain_path: _first_bad_receipt())
    result = runner.invoke(app, ["evidence", "verify-first-bad", str(tmp_path / "chain")])
    assert result.exit_code == 2
    assert "FIRST ATTRIBUTABLE BAD: 0.152.0" in result.stdout


def test_verify_first_bad_unresolved_exits_four(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("qualock.cli.verify_first_bad", lambda chain_path: _unresolved_receipt())
    result = runner.invoke(app, ["evidence", "verify-first-bad", str(tmp_path / "chain")])
    assert result.exit_code == 4


def test_verify_first_bad_domain_error_exits_three_with_bounded_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = FirstBadVerificationError(
        FirstBadVerificationReason.CHAIN_CLAIM_MISMATCH, "chain-receipt.json"
    )
    monkeypatch.setattr(
        "qualock.cli.verify_first_bad", lambda chain_path: (_ for _ in ()).throw(error)
    )
    result = runner.invoke(app, ["evidence", "verify-first-bad", str(tmp_path / "chain")])
    assert result.exit_code == 3
    assert "chain_claim_mismatch" in result.stdout


def test_verify_first_bad_unexpected_error_exits_one_without_leaking_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "qualock.cli.verify_first_bad",
        lambda chain_path: (_ for _ in ()).throw(RuntimeError("secret detail")),
    )
    result = runner.invoke(app, ["evidence", "verify-first-bad", str(tmp_path / "chain")])
    assert result.exit_code == 1
    assert "secret detail" not in result.stdout
    assert "first-bad verification failed" in result.stdout


def test_verify_first_bad_write_receipt_verifies_then_writes_create_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = tmp_path / "chain"
    receipt = _no_bad_receipt()
    order: list[str] = []

    def fake_verify(chain_path: Path):
        order.append("verify")
        return receipt

    def fake_write(chain_path: Path, value):
        order.append("write")
        assert chain_path == chain
        assert value == receipt
        return chain_path / "chain-receipt.json"

    monkeypatch.setattr("qualock.cli.verify_first_bad", fake_verify)
    monkeypatch.setattr("qualock.cli.write_first_bad_receipt", fake_write)

    result = runner.invoke(
        app, ["evidence", "verify-first-bad", str(chain), "--write-receipt"]
    )
    assert result.exit_code == 0
    assert order == ["verify", "write"]


def test_verify_first_bad_existing_receipt_checked_and_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = tmp_path / "chain"
    receipt = _no_bad_receipt()
    monkeypatch.setattr("qualock.cli.verify_first_bad", lambda chain_path: receipt)

    def refuse_overwrite(chain_path: Path, value):
        raise FirstBadVerificationError(
            FirstBadVerificationReason.MALFORMED_CHAIN_RECEIPT, "chain-receipt.json"
        )

    monkeypatch.setattr("qualock.cli.write_first_bad_receipt", refuse_overwrite)

    result = runner.invoke(
        app, ["evidence", "verify-first-bad", str(chain), "--write-receipt"]
    )
    assert result.exit_code == 3
    assert "malformed_chain_receipt" in result.stdout
