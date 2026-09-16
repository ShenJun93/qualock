BEHAVIORAL_CONTRACTS: frozenset[str] = frozenset(
    {"command.execution", "tool.inventory", "mcp.visibility", "approval.semantics"}
)


def validate_contract_id(value: str) -> str:
    if value not in BEHAVIORAL_CONTRACTS:
        raise ValueError(f"unknown behavioral contract: {value}")
    return value
