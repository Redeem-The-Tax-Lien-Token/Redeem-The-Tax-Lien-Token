"""
Offer price selection — §3 Step 4 (ADR-004).

Phase 1 (wholesale-only): returns wholesale offer_price when eligible.
Phase 2 adds BRRRR comparison: max(WHOLESALE_MAO, BRRRR_MAX_PRICE).

The system makes ONE offer — offer_policy determines which price.
Only `highest_eligible` is supported in phase 1.

Returns None when no exit is eligible (→ NURTURE).
"""

from __future__ import annotations

from . import _config
from .eligibility import WholesaleEligibility
from .wholesale import WholesaleCase


def select_offer_price(
    wholesale_case: WholesaleCase,
    wholesale_elig: WholesaleEligibility,
    cfg: dict | None = None,
) -> float | None:
    """
    Pick the offer price given the computed cases and eligibilities.

    Phase 1: wholesale-only.  Returns wholesale_case.offer_price when
    wholesale is eligible, else None (→ NURTURE).

    Args:
        wholesale_case: Computed WholesaleCase.
        wholesale_elig: Wholesale eligibility result.
        cfg:            strategy.yaml dict; loaded from disk if None.

    Returns:
        Offer price in dollars (already rounded to nearest $1k), or None.
    """
    if cfg is None:
        cfg = _config.load()

    policy = cfg.get("offer_policy", "highest_eligible")

    if policy not in ("highest_eligible", "highest_value"):
        raise ValueError(
            f"Unknown offer_policy {policy!r}. Valid: highest_eligible, highest_value"
        )

    if not wholesale_elig.all_pass:
        return None

    # Phase 1: only wholesale is implemented.
    # Phase 2 will add: max(wholesale_case.offer_price, brrrr_case.max_price)
    return wholesale_case.offer_price
