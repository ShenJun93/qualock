from dataclasses import dataclass
from enum import Enum
from typing import Literal

from packaging.version import Version
from pydantic import BaseModel, ConfigDict, field_validator

from qualock.qualification.models import QualificationResult, Verdict


class MonitorAction(str, Enum):
    NO_NEW_RELEASE = "no_new_release"
    NO_DOWNGRADE = "no_downgrade"
    ALREADY_QUALIFIED = "already_qualified"
    CHECKED = "checked"


class TerminalVerdict(str, Enum):
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"


class MonitorState(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    baseline_sha256: str
    agent: Literal["codex"] = "codex"
    candidate_version: str
    verdict: TerminalVerdict
    qualification_id: str
    completed_at: str

    @field_validator("candidate_version")
    @classmethod
    def validate_candidate_version(cls, value: str) -> str:
        Version(value)
        return value


@dataclass(frozen=True)
class MonitorOutcome:
    action: MonitorAction
    baseline_version: str
    latest_version: str
    qualification_result: QualificationResult | None = None
    recorded_verdict: Verdict | None = None
    state_persisted: bool | None = None
    state_warning: str | None = None
