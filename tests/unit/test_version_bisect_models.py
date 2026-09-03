from dataclasses import FrozenInstanceError

import pytest

from qualock.qualification.models import Verdict
from qualock.version_bisect.models import BisectOutcome, BisectStep, BisectStop


def test_models_are_frozen_and_reuse_verdict() -> None:
    step = BisectStep("0.152.0", "check-1", Verdict.PASS)
    outcome = BisectOutcome(
        bisect_id="bisect-test",
        baseline_version="0.151.0",
        upper_version="0.152.0",
        steps=(step,),
        last_known_good="0.152.0",
        first_bad=None,
        stop_reason=BisectStop.NO_BAD_FOUND,
    )
    assert outcome.steps[0].verdict is Verdict.PASS
    with pytest.raises(FrozenInstanceError):
        step.version = "0.153.0"  # type: ignore[misc]
