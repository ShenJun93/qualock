import json
from pathlib import Path

import pytest

from qualock.evidence.storage import ArtifactExistsError, write_qualification_artifacts
from tests.unit.test_report import sample_result


def test_writes_report_json_markdown_and_qualification_metadata(tmp_path: Path) -> None:
    root = write_qualification_artifacts(tmp_path, sample_result(), agent_display_name="Codex")
    assert (root / "report.md").is_file()
    assert (root / "report.json").is_file()
    assert (root / "qualification.json").is_file()
    report_payload = json.loads((root / "report.json").read_text(encoding="utf-8"))
    qualification_payload = json.loads((root / "qualification.json").read_text(encoding="utf-8"))
    assert report_payload["verdict"] == "block"
    assert "agent_display_name" not in report_payload
    assert "agent_display_name" not in qualification_payload


def test_refuses_to_overwrite_existing_qualification(tmp_path: Path) -> None:
    write_qualification_artifacts(tmp_path, sample_result(), agent_display_name="Codex")
    with pytest.raises(ArtifactExistsError):
        write_qualification_artifacts(tmp_path, sample_result(), agent_display_name="Codex")
