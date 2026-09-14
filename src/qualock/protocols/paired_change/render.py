"""Minimal stable terminal rendering for paired-change claim receipts."""

from .models import ClaimReceiptV1


def render_claim_receipt(receipt: ClaimReceiptV1) -> str:
    lines = [f"Qualification: {receipt.qualification_id}"]
    lines.extend(
        f"{claim.canary_id}: {claim.claim.value}" for claim in receipt.canary_claims
    )
    return "\n".join(lines) + "\n"
