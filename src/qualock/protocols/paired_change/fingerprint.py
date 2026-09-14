from typing import Any

from pydantic import BaseModel

from qualock.evidence.fingerprint import sha256_canonical


def digest_model(value: BaseModel) -> str:
    return sha256_canonical(value.model_dump(mode="json"))


def digest_value(value: Any) -> str:
    return sha256_canonical(value)
