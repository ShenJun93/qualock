from pathlib import Path

import yaml

from qualock.canary.loader import load_suite


ROOT = Path("benchmarks/oss-smoke")


def test_bundled_smoke_suite_loads_three_pinned_unique_canaries() -> None:
    manifest = yaml.safe_load((ROOT / "suite.yaml").read_text(encoding="utf-8"))
    paths = [ROOT / value for value in manifest["canaries"]]
    canaries = load_suite(paths)
    assert len(canaries) == 3
    assert len({item.id for item in canaries}) == 3
    for canary in canaries:
        assert len(canary.repository.base_sha) == 40
        assert canary.runtime.image.startswith("ghcr.io/astral-sh/uv:")
        assert canary.grader.patch.is_file()
        assert canary.grader.command
        assert canary.critical is True


def test_bundled_prompts_do_not_contain_upstream_patch_hint() -> None:
    manifest = yaml.safe_load((ROOT / "suite.yaml").read_text(encoding="utf-8"))
    canaries = load_suite([ROOT / value for value in manifest["canaries"]])
    forbidden = ["hostname and", "already_present", "__reduce_ex__", "__deepcopy__"]
    for canary in canaries:
        prompt = canary.task.lower()
        assert not any(token.lower() in prompt for token in forbidden)
