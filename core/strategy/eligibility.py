"""
Hard eligibility gates for wholesale and (stub) BRRRR — §3 Step 2.

Each gate is a separate boolean so the reason for ineligibility is
machine-readable (stored on the lead / shown in Gate A packet).

All thresholds come from config/strategy.yaml.  Nothing is hard-coded.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import _config
from .wholesale import WholesaleCase


# ── Ineligible reason codes ────────────────────────────────────────────────────

class IneligibleReason(str, Enum):
    SPREAD_TOO_THIN    = "SPREAD_TOO_THIN"     # MAO ≤ 0 — repairs eat entire spread
    FEE_TOO_LOW        = "FEE_TOO_LOW"         # fee < min_wholesale_fee
    LOW_BUYER_DEMAND   = "LOW_BUYER_DEMAND"    # not enough active qualified buyers
    TIMELINE_TOO_SHORT = "TIMELINE_TOO_SHORT"  # seller cannot wait for dispo window


# ── Wholesale eligibility ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class WholesaleEligibility:
    spread_ok:   bool   # MAO > 0
    fee_ok:      bool   # fee_at_offer >= min_wholesale_fee
    buyer_ok:    bool   # available_buyers >= min_buyer_count
    timeline_ok: bool   # seller_timeline_days >= min_seller_timeline_days
    all_pass:    bool   # every gate passed
    reasons:     tuple[str, ...]  # IneligibleReason values for every failed gate


def check_wholesale(
    case: WholesaleCase,
    available_buyers: int,
    seller_timeline_days: int,
    cfg: dict | None = None,
) -> WholesaleEligibility:
    """
    Run wholesale hard eligibility gates against a WholesaleCase.

    Args:
        case:                 Computed WholesaleCase (from compute_wholesale).
        available_buyers:     Count of active qualified buyers matching zip/price/tier.
        seller_timeline_days: How many days the seller will wait before they need to close.
        cfg:                  strategy.yaml dict; loaded from disk if None.

    Returns:
        WholesaleEligibility with per-gate booleans and a tuple of failure reasons.
    """
    if cfg is None:
        cfg = _config.load()

    w = cfg["wholesale"]
    min_fee     = float(w["min_wholesale_fee"])
    min_buyers  = int(w.get("min_buyer_count", 3))
    min_tl_days = int(w.get("min_seller_timeline_days", 21))

    spread_ok   = case.mao > 0
    fee_ok      = case.fee_at_offer >= min_fee
    buyer_ok    = available_buyers >= min_buyers
    timeline_ok = seller_timeline_days >= min_tl_days

    reasons: list[str] = []
    if not spread_ok:
        reasons.append(IneligibleReason.SPREAD_TOO_THIN)
    if not fee_ok:
        reasons.append(IneligibleReason.FEE_TOO_LOW)
    if not buyer_ok:
        reasons.append(IneligibleReason.LOW_BUYER_DEMAND)
    if not timeline_ok:
        reasons.append(IneligibleReason.TIMELINE_TOO_SHORT)

    return WholesaleEligibility(
        spread_ok=spread_ok,
        fee_ok=fee_ok,
        buyer_ok=buyer_ok,
        timeline_ok=timeline_ok,
        all_pass=not reasons,
        reasons=tuple(r.value for r in reasons),  # plain str, not enum instances
    )
