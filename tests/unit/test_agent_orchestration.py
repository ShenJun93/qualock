import pytest

from qualock.agents.orchestration import OrchestrationCapabilities, orchestration_capabilities


@pytest.mark.parametrize("agent", ["codex", "claude", "gemini"])
def test_orchestration_capabilities_support_mainline_agents(agent: str) -> None:
    caps = orchestration_capabilities(agent)
    assert (caps.release_discovery, caps.version_bisect, caps.github_pr, caps.first_bad) == (
        True,
        True,
        True,
        True,
    )


@pytest.mark.parametrize("agent", ["antigravity", "other", ""])
def test_orchestration_capabilities_fail_closed(agent: str) -> None:
    caps = orchestration_capabilities(agent)
    assert (caps.release_discovery, caps.version_bisect, caps.github_pr, caps.first_bad) == (
        False,
        False,
        False,
        False,
    )


def test_orchestration_capabilities_antigravity_first_bad_unsupported() -> None:
    caps = orchestration_capabilities("antigravity")
    assert caps.first_bad is False


def test_orchestration_capabilities_returns_frozen_dataclass_instance() -> None:
    caps = orchestration_capabilities("gemini")
    assert isinstance(caps, OrchestrationCapabilities)
    with pytest.raises(AttributeError):
        caps.release_discovery = False  # type: ignore[misc]


def test_orchestration_capabilities_default_construction_is_fail_closed() -> None:
    caps = OrchestrationCapabilities()
    assert (caps.release_discovery, caps.version_bisect, caps.github_pr, caps.first_bad) == (
        False,
        False,
        False,
        False,
    )
