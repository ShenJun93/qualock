from .models import ReadinessStatus, SetupPlan


def _recommended_action(plan: SetupPlan) -> str | None:
    for check in plan.readiness.checks:
        if check.status is ReadinessStatus.NEEDS_SETUP and check.recommendation:
            return check.recommendation
    return None


def render_setup_plan(plan: SetupPlan) -> str:
    detected = ", ".join(plan.capabilities.labels)
    lines = [
        "QuaLock Setup",
        "",
        f"Detected: {detected}",
        f"Protection level: {plan.level.value}",
    ]

    if plan.readiness.checks:
        lines.extend(["", "Environment"])
        for check in plan.readiness.checks:
            label = "OK" if check.status is ReadinessStatus.READY else "NEEDS SETUP"
            lines.append(f"- {label}: {check.name}")

    lines.extend(["", "Recommended protection"])
    for protection in plan.protections:
        lines.append(f"- {protection.name}")

    if plan.readiness.status is ReadinessStatus.NEEDS_SETUP:
        lines.extend(["", "QuaLock did not change your project."])
        action = _recommended_action(plan)
        if action:
            lines.extend(["", "Recommended action:"])
            if action in {"uv sync", "poetry install"}:
                lines.append(f"Run: {action}")
            else:
                lines.append(action)
        lines.append("Then run: qualock setup")

    lines.append("")
    return "\n".join(lines)
