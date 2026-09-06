import json
from pathlib import Path

import pytest

from qualock.qualification.models import Verdict
from qualock.version_bisect.models import BisectStep, BisectStop
from qualock.version_bisect.storage import FileBisectSummaryStore


def test_create_and_save_summary_atomically(tmp_path: Path) -> None:
    store = FileBisectSummaryStore(tmp_path)

    run_dir = store.create(
        bisect_id="bisect-test",
        baseline="0.151.0",
        upper="0.153.0",
        candidates=("0.152.0", "0.153.0"),
        steps=(),
        last_good="0.151.0",
        first_bad=None,
        stop=None,
        agent="claude",
    )

    created = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert created == {
        "schema_version": 2,
        "bisect_id": "bisect-test",
        "baseline_version": "0.151.0",
        "upper_version": "0.153.0",
        "candidates": ["0.152.0", "0.153.0"],
        "steps": [],
        "last_known_good": "0.151.0",
        "first_bad": None,
        "stop_reason": None,
        "agent": "claude",
    }
    assert created["schema_version"] == 2
    assert created["agent"] == "claude"

    store.save(
        bisect_id="bisect-test",
        baseline="0.151.0",
        upper="0.153.0",
        candidates=("0.152.0", "0.153.0"),
        steps=(BisectStep("0.152.0", "check-1", Verdict.BLOCK),),
        last_good="0.151.0",
        first_bad="0.152.0",
        stop=BisectStop.FIRST_BAD_FOUND,
        agent="claude",
    )

    saved = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2
    assert saved["agent"] == "claude"
    assert saved["steps"] == [
        {
            "version": "0.152.0",
            "qualification_id": "check-1",
            "verdict": "block",
        }
    ]
    assert saved["first_bad"] == "0.152.0"
    assert saved["stop_reason"] == "first_bad_found"
    assert list(run_dir.glob(".*.tmp")) == []


def test_create_refuses_to_overwrite_existing_summary(tmp_path: Path) -> None:
    run_dir = tmp_path / "bisect-test"
    run_dir.mkdir()
    summary = run_dir / "summary.json"
    summary.write_text("preserve", encoding="utf-8")

    store = FileBisectSummaryStore(tmp_path)
    with pytest.raises(FileExistsError):
        store.create(
            bisect_id="bisect-test",
            baseline="0.151.0",
            upper="0.153.0",
            candidates=("0.152.0", "0.153.0"),
            steps=(),
            last_good="0.151.0",
            first_bad=None,
            stop=None,
            agent="codex",
        )

    assert summary.read_text(encoding="utf-8") == "preserve"


def test_historical_v1_summary_is_never_migrated_or_rewritten(tmp_path: Path) -> None:
    legacy_dir = tmp_path / "bisect-legacy"
    legacy_dir.mkdir()
    legacy_path = legacy_dir / "summary.json"
    legacy_bytes = b'{"schema_version":1,"bisect_id":"legacy"}\n'
    legacy_path.write_bytes(legacy_bytes)

    store = FileBisectSummaryStore(tmp_path)
    run_dir = store.create(
        bisect_id="bisect-new",
        baseline="0.151.0",
        upper="0.153.0",
        candidates=("0.152.0", "0.153.0"),
        steps=(),
        last_good="0.151.0",
        first_bad=None,
        stop=None,
        agent="codex",
    )
    store.save(
        bisect_id="bisect-new",
        baseline="0.151.0",
        upper="0.153.0",
        candidates=("0.152.0", "0.153.0"),
        steps=(BisectStep("0.152.0", "check-1", Verdict.PASS),),
        last_good="0.152.0",
        first_bad=None,
        stop=None,
        agent="codex",
    )

    assert legacy_path.read_bytes() == legacy_bytes
    new_summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert new_summary["schema_version"] == 2
    assert new_summary["agent"] == "codex"
