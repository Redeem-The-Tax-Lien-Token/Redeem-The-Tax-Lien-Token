"""
Contractor qualification and bid-ranking logic for Agent 13.

All evaluation is deterministic Python.  The LLM is never involved in
qualification decisions — it may draft solicitation messages, but
pass/fail on each criterion is computed here.

Public API:
    ContractorQualResult(qualified, reasons)
    qualify_contractor(contractor) -> ContractorQualResult
    rank_bids(qualified_bids, deal_budget) -> list[dict]
    BID_STATUS_SELECTED         — sentinel string
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

BID_STATUS_SELECTED = "selected"

_REQUIRE_LICENSE    = True   # patchable in tests
_REQUIRE_INSURANCE  = True   # patchable in tests


@dataclass(frozen=True)
class ContractorQualResult:
    qualified: bool
    reasons:   list[str] = field(default_factory=list)


def qualify_contractor(contractor: dict, today: date | None = None) -> ContractorQualResult:
    """
    Determine if a contractor is eligible to receive a deal assignment.

    contractor dict keys:
        license_verified   bool
        insurance_verified bool
        insurance_expiry   date | str | None
        active             bool
        tier_rating        'preferred' | 'approved' | 'probation' | None
    """
    today = today or datetime.now(tz=timezone.utc).date()
    reasons: list[str] = []

    if not contractor.get("active", True):
        reasons.append("Contractor is marked inactive")

    if _REQUIRE_LICENSE and not contractor.get("license_verified", False):
        reasons.append("License not verified")

    if _REQUIRE_INSURANCE and not contractor.get("insurance_verified", False):
        reasons.append("Insurance not verified")
    elif contractor.get("insurance_verified"):
        expiry = contractor.get("insurance_expiry")
        if expiry:
            expiry_date = (
                date.fromisoformat(str(expiry)) if not isinstance(expiry, date) else expiry
            )
            if expiry_date < today:
                reasons.append(f"Insurance expired on {expiry_date}")

    rating = contractor.get("tier_rating")
    if rating == "probation":
        reasons.append("Contractor is on probation — operator review required")

    return ContractorQualResult(qualified=not reasons, reasons=reasons)


def rank_bids(bids: list[dict], deal_budget: float) -> list[dict]:
    """
    Rank qualified contractor bids for a deal.

    Ranking priority:
      1. Bids ≤ deal_budget (budget compliance)
      2. Lower bid amount (save money)
      3. Shorter timeline (faster rehab)
      4. preferred tier rating over approved

    Returns the same list with a 'rank' key added (1 = best).
    Bids over budget are included but ranked last and flagged.
    """
    def sort_key(b: dict) -> tuple:
        over_budget  = 1 if float(b.get("bid_amount", 0)) > deal_budget else 0
        rating_order = {"preferred": 0, "approved": 1, "probation": 2, None: 3}
        rating       = b.get("tier_rating")
        return (
            over_budget,
            float(b.get("bid_amount", float("inf"))),
            int(b.get("timeline_days") or 999),
            rating_order.get(rating, 3),
        )

    sorted_bids = sorted(bids, key=sort_key)
    result = []
    for rank, bid in enumerate(sorted_bids, start=1):
        bid = dict(bid)
        bid["rank"] = rank
        bid["over_budget"] = float(bid.get("bid_amount", 0)) > deal_budget
        result.append(bid)
    return result
