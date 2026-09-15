"""CLI contract for offline first-bad/v1 chain verification."""

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qualock.agents.releases import ReleaseDiscoveryError
from qualock.baseline.io import BaselineStaleError
from qualock.canary.loader import CanaryLoadError
from qualock.cli import app
from qualock.commands import CommandError
from qualock.config.io import ConfigError
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
from qualock.protocols.first_bad.orchestrate import FirstBadOrchestrationOutcome
from qualock.protocols.first_bad.render import (
    render_first_bad_edge_line,
    render_first_bad_range,
    render_first_bad_receipt,
    render_first_bad_terminal,
    render_first_bad_title,
)
from qualock.protocols.paired_change.models import ClaimClass

runner = CliRunner()
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)

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
        "Within this exact frozen release-catalog snapshot.\n"
    )

def test_render_first_attributable_bad_core_text() -> None:
    text = render_first_bad_receipt(_first_bad_receipt())
    assert text == (
        "QuaLock First-Bad Verification\n"
        "Range: 0.150.0 -> 0.152.0\n"
        "0.150.0 -> 0.151.0  NO REGRESSION\n"
        "0.151.0 -> 0.152.0  ATTRIBUTABLE CHANGE\n"
        "FIRST ATTRIBUTABLE BAD: 0.152.0\n"
        "Within this exact frozen release-catalog snapshot.\n"
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


def test_verify_first_bad_help_exposes_write_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    result = runner.invoke(app, ["evidence", "verify-first-bad", "--help"])
    assert result.exit_code == 0
    stdout = _strip_ansi(result.stdout)
    assert "--write-receipt" in stdout
    assert "--protocol" not in stdout


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
    assert "Within this exact frozen release-catalog snapshot." in result.stdout


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


# --- progressive rendering (top-level `qualock first-bad`) ---------------------------


def test_render_first_bad_title() -> None:
    assert render_first_bad_title() == "QuaLock First-Bad Causal Scan\n"


def test_render_first_bad_range_prints_baseline_and_upper_once() -> None:
    assert render_first_bad_range("Codex", "0.150.0", "0.153.0") == (
        "Codex baseline 0.150.0 -> upper 0.153.0\n"
    )


def test_render_first_bad_edge_line_no_regression() -> None:
    edge = _edge(0, "0.150.0", "0.151.0", EdgeClassification.NO_REGRESSION_OBSERVED)
    assert render_first_bad_edge_line(edge) == "0.150.0 -> 0.151.0  NO REGRESSION\n"


def test_render_first_bad_edge_line_attributable_change() -> None:
    edge = _edge(1, "0.151.0", "0.152.0", EdgeClassification.ATTRIBUTABLE_CHANGESET)
    assert render_first_bad_edge_line(edge) == "0.151.0 -> 0.152.0  ATTRIBUTABLE CHANGE\n"


def test_render_first_bad_terminal_first_attributable_bad() -> None:
    text = render_first_bad_terminal(_first_bad_receipt())
    assert text == (
        "FIRST ATTRIBUTABLE BAD: 0.152.0\n"
        "Within this exact frozen release-catalog snapshot.\n"
    )

def test_render_first_bad_terminal_no_bad_found_does_not_overclaim() -> None:
    text = render_first_bad_terminal(_no_bad_receipt())
    assert text == "No attributable bad found within this exact frozen release-catalog snapshot.\n"


def test_render_first_bad_terminal_unresolved_scoped_to_frozen_range() -> None:
    text = render_first_bad_terminal(_unresolved_receipt())
    assert text == "No first-attributable-bad claim can be made beyond this edge.\n"


# --- top-level `qualock first-bad` CLI -----------------------------------------------


class _FakePreflight:
    def __init__(self, agent_name: str, baseline_version: str, upper_version: str) -> None:
        self.agent_name = agent_name
        self.catalog = (baseline_version, upper_version)
        self.upper_version = upper_version


def _fake_outcome(
    *, agent_name: str, baseline_version: str, upper_version: str, receipt: FirstBadReceiptV1
) -> FirstBadOrchestrationOutcome:
    return FirstBadOrchestrationOutcome(
        first_bad_id="first-bad-20260915T120000Z-aaaaaaaa",
        agent_name=agent_name,
        baseline_version=baseline_version,
        upper_version=upper_version,
        package_path=Path("/tmp/does-not-exist/.qualock/results/first-bad-20260915T120000Z-aaaaaaaa"),
        receipt=receipt,
    )


def _invoke_first_bad(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: FirstBadOrchestrationOutcome,
    edges: tuple[FirstBadEdgeSummaryV1, ...],
    *args: str,
):
    monkeypatch.chdir(tmp_path)

    def fake_execute_first_bad(root: Path, upper_spec: str, *, on_start=None, on_edge=None, **kwargs):
        preflight = _FakePreflight(
            outcome.agent_name, outcome.baseline_version, outcome.upper_version
        )
        if on_start is not None:
            on_start(preflight, tmp_path / "staging")
        if on_edge is not None:
            for summary in edges:
                on_edge(summary)
        return outcome

    monkeypatch.setattr("qualock.cli.execute_first_bad", fake_execute_first_bad)
    return runner.invoke(app, ["first-bad", *args])


def test_first_bad_cli_no_bad_prints_progress_and_package_path_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = _no_bad_receipt()
    outcome = _fake_outcome(
        agent_name="codex", baseline_version="0.150.0", upper_version="0.152.0", receipt=receipt
    )
    result = _invoke_first_bad(tmp_path, monkeypatch, outcome, receipt.edges, "codex@0.152.0")

    assert "QuaLock First-Bad Causal Scan" in result.stdout
    assert "Codex baseline 0.150.0 -> upper 0.152.0" in result.stdout
    assert "0.150.0 -> 0.151.0  NO REGRESSION" in result.stdout
    assert "0.151.0 -> 0.152.0  NO REGRESSION" in result.stdout
    assert "No attributable bad found within this exact frozen release-catalog snapshot." in result.stdout
    assert str(outcome.package_path) in result.stdout
    assert result.exit_code == 0


def test_first_bad_cli_first_attributable_bad_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = _first_bad_receipt()
    outcome = _fake_outcome(
        agent_name="codex", baseline_version="0.150.0", upper_version="0.152.0", receipt=receipt
    )
    result = _invoke_first_bad(tmp_path, monkeypatch, outcome, receipt.edges, "codex@0.152.0")

    assert "FIRST ATTRIBUTABLE BAD: 0.152.0" in result.stdout
    assert "Within this exact frozen release-catalog snapshot." in result.stdout
    assert str(outcome.package_path) in result.stdout
    assert result.exit_code == 2


def test_first_bad_cli_unresolved_exits_four(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = _unresolved_receipt()
    outcome = _fake_outcome(
        agent_name="codex", baseline_version="0.150.0", upper_version="0.151.0", receipt=receipt
    )
    result = _invoke_first_bad(tmp_path, monkeypatch, outcome, receipt.edges, "codex@0.151.0")

    assert "No first-attributable-bad claim can be made beyond this edge." in result.stdout
    assert str(outcome.package_path) in result.stdout
    assert result.exit_code == 4


def test_first_bad_cli_package_path_absent_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "qualock.cli.execute_first_bad",
        lambda root, upper_spec, **kwargs: (_ for _ in ()).throw(CommandError("bad input")),
    )
    result = runner.invoke(app, ["first-bad", "codex@0.152.0"])
    assert result.exit_code == 3
    assert ".qualock/results" not in result.stdout


@pytest.mark.parametrize(
    "exc",
    [
        CommandError("bad input"),
        ConfigError("bad config"),
        CanaryLoadError("bad canary"),
        FileNotFoundError("missing"),
        BaselineStaleError("stale"),
    ],
)
def test_first_bad_cli_input_errors_exit_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "qualock.cli.execute_first_bad",
        lambda root, upper_spec, **kwargs: (_ for _ in ()).throw(exc),
    )
    result = runner.invoke(app, ["first-bad", "codex@0.152.0"])
    assert result.exit_code == 3
    assert str(exc) in result.stdout


def test_first_bad_cli_release_discovery_error_exits_one_with_literal_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "qualock.cli.execute_first_bad",
        lambda root, upper_spec, **kwargs: (_ for _ in ()).throw(
            ReleaseDiscoveryError("catalog unavailable")
        ),
    )
    result = runner.invoke(app, ["first-bad", "codex@0.152.0"])
    assert result.exit_code == 1
    assert "catalog unavailable" in result.stdout


def test_first_bad_cli_unexpected_operational_error_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "qualock.cli.execute_first_bad",
        lambda root, upper_spec, **kwargs: (_ for _ in ()).throw(OSError("disk full [literal]")),
    )
    result = runner.invoke(app, ["first-bad", "codex@0.152.0"])
    assert result.exit_code == 1
    assert "disk full [literal]" in result.stdout


def test_first_bad_cli_generated_package_verification_failure_exits_one_generic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    error = FirstBadVerificationError(
        FirstBadVerificationReason.CHAIN_CLAIM_MISMATCH, "chain-receipt.json"
    )
    monkeypatch.setattr(
        "qualock.cli.execute_first_bad",
        lambda root, upper_spec, **kwargs: (_ for _ in ()).throw(error),
    )
    result = runner.invoke(app, ["first-bad", "codex@0.152.0"])
    assert result.exit_code == 1
    assert "chain-receipt.json" not in result.stdout
    assert "chain_claim_mismatch" not in result.stdout
    assert "first-bad" in result.stdout.lower()


def test_first_bad_cli_requires_upper_spec() -> None:
    result = runner.invoke(app, ["first-bad"])
    assert result.exit_code != 0
