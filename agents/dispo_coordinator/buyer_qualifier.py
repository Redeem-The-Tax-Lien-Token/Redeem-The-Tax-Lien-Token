"""
Buyer qualification logic for Agent 11.

Deterministic Python — no LLM. Takes a buyer's offer data and checks:
  1. Proof of funds confirmed
  2. Close timeline ≤ our remaining inspection + closing window
  3. EMD capacity meets the deal's EMD requirement

Public API:
    result = qualify_buyer(offer, deal, cfg)
    # result.qualified: bool
    # result.reasons: tuple[str, ...]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QualificationResult:
    qualified: bool
    reasons: tuple[str, ...]

    @property
    def disqualified(self) -> bool:
        return not self.qualified


_REQUIRE_POF           = True   # flip to False in tests if needed
_DEFAULT_MAX_TIMELINE  = 45     # days — longest acceptable buyer timeline
_DEFAULT_MIN_EMD       = 500    # USD — minimum EMD a buyer must be able to put up


def qualify_buyer(
    offer: dict[str, Any],
    deal: dict[str, Any],
    cfg: dict[str, Any] | None = None,
) -> QualificationResult:
    """
    Qualify a single buyer offer against the deal requirements.

    Parameters
    ----------
    offer : dict  — row from buyer_offers (offer_amount, pof_confirmed,
                    timeline_days, emd_capacity)
    deal  : dict  — deal row (closing_date, emd_amount, offer_amount)
    cfg   : dict  — parsed strategy.yaml (reads contract.closing_window_days)

    Returns
    -------
    QualificationResult(qualified, reasons)
    """
    reasons: list[str] = []
    contract_cfg = (cfg or {}).get("contract", {})

    # 1. Proof of funds
    if _REQUIRE_POF and not offer.get("pof_confirmed"):
        reasons.append("POF_NOT_CONFIRMED")

    # 2. Timeline — buyer must be able to close within the deal's closing window
    timeline = offer.get("timeline_days")
    if timeline is not None:
        max_tl = contract_cfg.get("closing_window_days", {}).get("wholesale", _DEFAULT_MAX_TIMELINE)
        if isinstance(max_tl, dict):
            max_tl = max_tl.get("wholesale", _DEFAULT_MAX_TIMELINE)
        if timeline > int(max_tl):
            reasons.append(f"TIMELINE_TOO_LONG ({timeline}d > {max_tl}d)")

    # 3. EMD capacity must meet deal requirement
    required_emd = float(deal.get("emd_amount") or contract_cfg.get("emd_amount", _DEFAULT_MIN_EMD))
    buyer_emd = offer.get("emd_capacity")
    if buyer_emd is not None and float(buyer_emd) < required_emd:
        reasons.append(f"EMD_INSUFFICIENT (${float(buyer_emd):,.0f} < ${required_emd:,.0f})")

    # 4. Offer must be non-zero
    offer_amount = offer.get("offer_amount")
    if not offer_amount or float(offer_amount) <= 0:
        reasons.append("OFFER_AMOUNT_MISSING")

    return QualificationResult(qualified=len(reasons) == 0, reasons=tuple(reasons))


def rank_offers(
    qualified_offers: list[dict[str, Any]],
    deal: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Rank qualified buyer offers for a deal.

    Ranking criteria (descending priority):
      1. Offer amount (highest first)
      2. EMD capacity (highest first, as tie-breaker)
      3. Timeline (shortest first, as secondary tie-breaker)

    Returns the list sorted best-first, with each item having a 'ranked' int (1=best).
    """
    sorted_offers = sorted(
        qualified_offers,
        key=lambda o: (
            -float(o.get("offer_amount") or 0),
            -float(o.get("emd_capacity") or 0),
             int(o.get("timeline_days") or 9999),
        ),
    )
    result = []
    for rank, offer in enumerate(sorted_offers, start=1):
        result.append({**offer, "ranked": rank})
    return result
