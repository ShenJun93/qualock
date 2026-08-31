import hashlib
from collections.abc import Sequence
from pathlib import Path

from qualock.canary.loader import load_suite
from qualock.canary.models import CanarySpec
from qualock.config.io import load_config
from qualock.config.models import QualockConfig
from qualock.evidence.fingerprint import sha256_canonical


def project_dir(root: Path) -> Path:
    return root.resolve() / ".qualock"


def load_project(root: Path) -> tuple[QualockConfig, list[CanarySpec]]:
    root = root.resolve()
    config = load_config(project_dir(root) / "config.yaml")
    paths: list[Path] = []
    for pattern in config.canary_globs:
        paths.extend(sorted(root.glob(pattern)))
    return config, load_suite(paths)


def config_fingerprint(config: QualockConfig) -> str:
    return sha256_canonical(config.model_dump(mode="json"))


def suite_fingerprint(canaries: Sequence[CanarySpec]) -> str:
    payload: list[dict[str, object]] = []
    for canary in sorted(canaries, key=lambda item: item.id):
        item = canary.model_dump(mode="json")
        grader = dict(item["grader"])
        patch = canary.grader.patch
        grader.pop("patch", None)
        grader["patch_sha256"] = hashlib.sha256(patch.read_bytes()).hexdigest()
        item["grader"] = grader
        payload.append(item)
    return sha256_canonical(payload)
