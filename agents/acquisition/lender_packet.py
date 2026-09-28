"""
Lender packet builder for Agent 12.

Reads lender profiles from lending.yaml, validates their age (fail-closed if
> 45 days), and produces a structured packet for each acquisition lender,
suitable for submission via adapters.lender.

Public API:
    load_acquisition_lenders(cfg_path=None) -> list[dict]
    validate_lender_profiles(profiles) -> list[str]   # returns error messages
    build_lender_packet(deal, lender_profile) -> dict
    recommend_lender(profiles, deal) -> dict | None
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

_DEFAULT_LENDING_PATH = Path(__file__).parent.parent.parent / "config" / "lending.yaml"
_MAX_PROFILE_AGE_DAYS = 45


def _today() -> date:
    return datetime.now(tz=timezone.utc).date()


def load_acquisition_lenders(cfg_path: Path | None = None) -> list[dict]:
    """Load and return acquisition lender profiles from lending.yaml."""
    path = cfg_path or _DEFAULT_LENDING_PATH
    cfg  = yaml.safe_load(path.read_text())
    return cfg.get("acquisition_lenders", [])


def validate_lender_profiles(profiles: list[dict], today: date | None = None) -> list[str]:
    """
    Validate lender profiles.  Returns a list of error strings.
    Empty list → all profiles valid.

    Fail-closed: a profile older than _MAX_PROFILE_AGE_DAYS is an error.
    """
    errors: list[str] = []
    today  = today or _today()

    for p in profiles:
        pid = p.get("id", "<unknown>")

        if "verified_at" not in p:
            errors.append(f"Lender '{pid}': missing verified_at")
            continue

        try:
            verified = date.fromisoformat(str(p["verified_at"]))
        except ValueError:
            errors.append(f"Lender '{pid}': invalid verified_at '{p['verified_at']}'")
            continue

        age = (today - verified).days
        if age > _MAX_PROFILE_AGE_DAYS:
            errors.append(
                f"Lender '{pid}': profile is {age} days old "
                f"(max {_MAX_PROFILE_AGE_DAYS}). Update verified_at before use."
            )

    return errors


def build_lender_packet(deal: dict, lender_profile: dict) -> dict:
    """
    Build the submission packet for one lender.

    deal keys used: purchase_price, repair_estimate, address, city, arv,
                    strategy, attom_id (optional)
    """
    purchase    = float(deal.get("purchase_price") or 0)
    repairs     = float(deal.get("repair_estimate") or 0)
    arv         = float(deal.get("arv") or 0)
    ltc_p       = float(lender_profile.get("ltc_purchase", 0.90))
    ltc_r       = float(lender_profile.get("ltc_rehab", 1.00))

    loan_purchase = purchase * ltc_p
    loan_rehab    = repairs  * ltc_r
    total_loan    = loan_purchase + loan_rehab

    min_loan = float(lender_profile.get("min_loan", 0))
    max_loan = float(lender_profile.get("max_loan", float("inf")))
    total_loan = max(min_loan, min(total_loan, max_loan))

    return {
        "lender_id":       lender_profile["id"],
        "lender_label":    lender_profile.get("label", ""),
        "property_address": deal.get("address", ""),
        "property_city":   deal.get("city", ""),
        "purchase_price":  purchase,
        "repair_estimate": repairs,
        "arv":             arv,
        "loan_requested":  round(total_loan, 2),
        "rate":            lender_profile.get("rate"),
        "points":          lender_profile.get("points"),
        "fixed_fees":      lender_profile.get("fixed_fees"),
        "ltc_purchase":    ltc_p,
        "ltc_rehab":       ltc_r,
        "min_loan":        min_loan,
        "max_loan":        max_loan,
    }


def recommend_lender(profiles: list[dict], deal: dict, today: date | None = None) -> dict | None:
    """
    Pick the acquisition lender with the lowest all-in financing cost for this deal.

    Returns the lender profile dict, or None if no valid profiles exist.
    All-in cost = (loan × points) + fixed_fees + (loan × rate × est_hold_months / 12).
    est_hold_months defaults to 8 (conservative for BRRRR).
    """
    today            = today or _today()
    est_hold_months  = 8
    valid_errors     = validate_lender_profiles(profiles, today)

    # Build a set of invalid profile ids
    invalid_ids: set[str] = set()
    for err in valid_errors:
        for p in profiles:
            if f"Lender '{p['id']}':" in err:
                invalid_ids.add(p["id"])

    purchase = float(deal.get("purchase_price") or 0)
    repairs  = float(deal.get("repair_estimate") or 0)

    best_cost:    float | None = None
    best_profile: dict | None = None

    for p in profiles:
        if p["id"] in invalid_ids:
            continue

        loan = purchase * float(p.get("ltc_purchase", 0.9)) + repairs * float(p.get("ltc_rehab", 1.0))
        loan = max(float(p.get("min_loan", 0)), min(loan, float(p.get("max_loan", float("inf")))))

        cost = (
            loan * float(p.get("points", 0))
            + float(p.get("fixed_fees", 0))
            + loan * float(p.get("rate", 0)) * est_hold_months / 12
        )

        if best_cost is None or cost < best_cost:
            best_cost    = cost
            best_profile = p

    return best_profile
