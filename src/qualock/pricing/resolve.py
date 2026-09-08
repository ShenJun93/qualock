import json

from qualock.pricing.catalog import RATE_CARDS
from qualock.pricing.models import ModelIdentity
from qualock.qualification.models import QualificationResult

_PROVIDERS = {"codex": "openai", "claude": "anthropic", "antigravity": "google"}
_OPENAI_ALIASES = {"gpt-5.6": "gpt-5.6-sol"}
_ANTIGRAVITY_MODELS = {
    "gemini-3.8-flash-low": "gemini-3.8-flash",
    "gemini-3.8-flash-medium": "gemini-3.8-flash",
    "gemini-3.8-flash-high": "gemini-3.8-flash",
}

_UNAVAILABLE = "unavailable"
_UNKNOWN_MODEL = "unknown_model"
_MISSING_OBSERVED_MODEL = "missing_observed_model"
_INCONSISTENT_OBSERVED_MODEL = "inconsistent_observed_model"
_MALFORMED_MODEL_EVIDENCE = "malformed_model_evidence"


def provider_for_agent(agent: str) -> str | None:
    return _PROVIDERS.get(agent)


def _canonical_models_for(provider: str) -> frozenset[str]:
    return frozenset(card.canonical_model for card in RATE_CARDS if card.provider == provider)


def _extract_model_value(container: dict[str, object]) -> tuple[str | None, bool]:
    """Return (observed_model, is_malformed) for a single model field."""
    if "model" not in container:
        return None, False
    value = container["model"]
    if value is None:
        return None, False
    if not isinstance(value, str):
        return None, True
    if not value.strip():
        return None, False
    return value, False


def _scan_claude_observations(result: QualificationResult) -> tuple[str | None, str | None]:
    """Return (agreed_model, failure_reason); failure_reason is None on success."""
    observed: set[str] = set()
    for execution in result.executions:
        for attempt in execution.attempts:
            for raw_line in attempt.events_jsonl.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    return None, _MALFORMED_MODEL_EVIDENCE
                if not isinstance(event, dict):
                    return None, _MALFORMED_MODEL_EVIDENCE

                event_type = event.get("type")
                candidate: str | None = None
                if event_type == "system" and event.get("subtype") == "init":
                    candidate, malformed = _extract_model_value(event)
                    if malformed:
                        return None, _MALFORMED_MODEL_EVIDENCE
                elif event_type == "assistant":
                    message = event.get("message")
                    if isinstance(message, dict):
                        candidate, malformed = _extract_model_value(message)
                        if malformed:
                            return None, _MALFORMED_MODEL_EVIDENCE

                if candidate is not None:
                    observed.add(candidate)

    if not observed:
        return None, None
    if len(observed) > 1:
        return None, _INCONSISTENT_OBSERVED_MODEL
    return next(iter(observed)), None


def _resolve_claude(configured_model: str, result: QualificationResult) -> ModelIdentity:
    canonical_models = _canonical_models_for("anthropic")
    observed_model, failure = _scan_claude_observations(result)
    if failure is not None:
        return ModelIdentity(None, _UNAVAILABLE, failure)

    if observed_model is not None:
        if observed_model not in canonical_models:
            return ModelIdentity(None, _UNAVAILABLE, _UNKNOWN_MODEL)
        if configured_model in canonical_models and configured_model != observed_model:
            return ModelIdentity(None, _UNAVAILABLE, _INCONSISTENT_OBSERVED_MODEL)
        return ModelIdentity(observed_model, "runtime_observed", None)

    if configured_model in canonical_models:
        return ModelIdentity(configured_model, "configured_exact", None)

    return ModelIdentity(None, _UNAVAILABLE, _MISSING_OBSERVED_MODEL)


def _resolve_openai(configured_model: str) -> ModelIdentity:
    canonical_models = _canonical_models_for("openai")
    if configured_model in canonical_models:
        return ModelIdentity(configured_model, "configured_exact", None)
    alias_target = _OPENAI_ALIASES.get(configured_model)
    if alias_target is not None:
        return ModelIdentity(alias_target, "documented_alias", None)
    return ModelIdentity(None, _UNAVAILABLE, _UNKNOWN_MODEL)


def _resolve_antigravity(configured_model: str) -> ModelIdentity:
    canonical_model = _ANTIGRAVITY_MODELS.get(configured_model)
    if canonical_model is not None:
        return ModelIdentity(canonical_model, "agent_exact_mapping", None)
    return ModelIdentity(None, _UNAVAILABLE, _UNKNOWN_MODEL)


def resolve_model_identity(
    agent: str, configured_model: str, result: QualificationResult
) -> ModelIdentity:
    provider = provider_for_agent(agent)
    if provider == "anthropic":
        return _resolve_claude(configured_model, result)
    if provider == "openai":
        return _resolve_openai(configured_model)
    if provider == "google":
        return _resolve_antigravity(configured_model)
    return ModelIdentity(None, _UNAVAILABLE, _UNKNOWN_MODEL)
