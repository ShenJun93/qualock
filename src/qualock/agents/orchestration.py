from dataclasses import dataclass


@dataclass(frozen=True)
class OrchestrationCapabilities:
    release_discovery: bool = False
    version_bisect: bool = False
    github_pr: bool = False
    first_bad: bool = False


_SUPPORTED = OrchestrationCapabilities(
    release_discovery=True,
    version_bisect=True,
    github_pr=True,
    first_bad=True,
)
_UNSUPPORTED = OrchestrationCapabilities()

_SUPPORTED_AGENTS = frozenset({"codex", "claude", "gemini"})


def orchestration_capabilities(agent_name: str) -> OrchestrationCapabilities:
    return _SUPPORTED if agent_name in _SUPPORTED_AGENTS else _UNSUPPORTED
