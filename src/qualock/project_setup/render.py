from .models import SetupPlan


def render_setup_plan(plan: SetupPlan) -> str:
    detected = ", ".join(plan.capabilities.labels)
    lines = [
        "QuaLock Setup",
        "",
        f"Detected: {detected}",
        f"Protection level: {plan.level.value}",
        "",
        "Recommended protection",
    ]
    for protection in plan.protections:
        lines.append(f"- {protection.name}")
    lines.append("")
    return "\n".join(lines)
