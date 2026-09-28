"""
Draw-request logic for Agent 13.

Every draw request requires Gate B (operator approval) before funds are
released.  This module contains only the deterministic validation logic.
The actual Gate B queue interaction uses the existing gate_b_queue module
in agents/dispo_coordinator (which handles all Gate B approvals across both
wholesale and BRRRR tracks).

Public API:
    DrawValidationResult(valid, errors)
    validate_draw_request(draw, deal_budget, draws_so_far) -> DrawValidationResult
    total_drawn(draws) -> float
    remaining_budget(deal_budget, draws) -> float
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DrawValidationResult:
    valid:  bool
    errors: list[str] = field(default_factory=list)


def validate_draw_request(
    draw: dict,
    deal_budget: float,
    draws_so_far: list[dict],
) -> DrawValidationResult:
    """
    Validate a new draw request before sending to Gate B.

    draw keys: amount_requested (float), description (str), draw_number (int)
    draws_so_far: list of previously approved/pending draws (with amount_requested)

    Rules (fail-closed):
      - amount_requested must be > 0
      - description must be non-empty
      - draw_number must be unique
      - total of all draws (including this one) must not exceed deal_budget
    """
    errors: list[str] = []

    amount = float(draw.get("amount_requested") or 0)
    if amount <= 0:
        errors.append("amount_requested must be > 0")

    if not (draw.get("description") or "").strip():
        errors.append("description is required")

    draw_number = draw.get("draw_number")
    existing_numbers = {d.get("draw_number") for d in draws_so_far}
    if draw_number in existing_numbers:
        errors.append(f"draw_number {draw_number} already exists for this deal")

    already_drawn = total_drawn(draws_so_far)
    if deal_budget > 0 and already_drawn + amount > deal_budget:
        overage = already_drawn + amount - deal_budget
        errors.append(
            f"Draw would exceed approved budget by ${overage:,.2f}. "
            f"Approved budget: ${deal_budget:,.2f}. "
            f"Already drawn: ${already_drawn:,.2f}. "
            f"This draw: ${amount:,.2f}. "
            "Submit a change order to increase the budget."
        )

    return DrawValidationResult(valid=not errors, errors=errors)


def total_drawn(draws: list[dict]) -> float:
    """Sum of amount_requested for all draws (regardless of approval status)."""
    return round(sum(float(d.get("amount_requested") or 0) for d in draws), 2)


def remaining_budget(deal_budget: float, draws: list[dict]) -> float:
    return round(deal_budget - total_drawn(draws), 2)
