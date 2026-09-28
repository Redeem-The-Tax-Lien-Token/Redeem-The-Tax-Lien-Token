"""
Wholesale deal calculator — §2.2 of CLAUDE.md.

All math is deterministic Python. No LLM involvement here.

Formula (§2.2):
    WHOLESALE_MAO = (ARV × discount_rate) − repairs − assignment_fee

The offer price is MAO rounded down to the nearest $1,000.  Rounding keeps
the price clean and adds a small buffer to the assignment fee.

Usage:
    from core.strategy.wholesale import compute_wholesale
    case = compute_wholesale(arv=200_000, repairs=20_000, neighborhood_class="A")
    print(case.offer_price, case.fee_at_offer)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import _config


@dataclass(frozen=True)
class WholesaleCase:
    # ── inputs ────────────────────────────────────────────────────────────────
    arv:                float
    repairs:            float
    neighborhood_class: str     # "A" | "B" | "C" | "STD"
    assignment_fee:     float   # target fee baked into MAO (from config)

    # ── computed ──────────────────────────────────────────────────────────────
    discount_rate: float        # fraction, e.g. 0.75
    mao:           float        # raw MAO before rounding (may be ≤ 0)
    offer_price:   float        # mao rounded down to $1k; 0 when mao ≤ 0
    fee_at_offer:  float        # arv*rate − repairs − offer_price


def compute_wholesale(
    arv: float,
    repairs: float,
    neighborhood_class: str,
    cfg: dict | None = None,
) -> WholesaleCase:
    """
    Compute the wholesale deal case for a property.

    Args:
        arv:                After-repair value in dollars.
        repairs:            Estimated repair cost in dollars.
        neighborhood_class: "A", "B", "C", or "STD".
        cfg:                strategy.yaml dict; loaded from disk if None.

    Returns:
        WholesaleCase with all computed fields.

    Raises:
        ValueError: if neighborhood_class is not in the config's discount_rates.
    """
    if cfg is None:
        cfg = _config.load()

    w = cfg["wholesale"]
    rate_map: dict[str, float] = w["discount_rates"]
    key = neighborhood_class.upper()
    discount_rate = rate_map.get(key)
    if discount_rate is None:
        valid = sorted(rate_map)
        raise ValueError(
            f"Unknown neighborhood_class {neighborhood_class!r}. Valid: {valid}"
        )

    assignment_fee = float(w["assignment_fee"])

    # §2.2 formula
    mao = arv * discount_rate - repairs - assignment_fee

    # Round offer down to nearest $1,000; never negative
    offer_price = math.floor(mao / 1_000) * 1_000 if mao > 0 else 0.0

    # Actual fee the system collects at this offer price
    fee_at_offer = arv * discount_rate - repairs - offer_price

    return WholesaleCase(
        arv=arv,
        repairs=repairs,
        neighborhood_class=key,
        assignment_fee=assignment_fee,
        discount_rate=discount_rate,
        mao=mao,
        offer_price=offer_price,
        fee_at_offer=fee_at_offer,
    )
