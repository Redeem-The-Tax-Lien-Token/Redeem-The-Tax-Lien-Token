"""
Seasoning eligibility logic for Agent 15.

Computes months_owned from the acquisition recorded date and checks whether
the deal meets a given lender's seasoning requirements.

Public API:
    SeasoningResult(eligible, months_owned, value_basis_rule, reason)
    check_seasoning(acquisition_date, lender_profile, now=None) -> SeasoningResult
    months_between(start, end) -> int
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal


ValueBasisRule = Literal["full_value", "cost_basis_value", "ineligible"]


@dataclass(frozen=True)
class SeasoningResult:
    eligible:         bool
    months_owned:     int
    value_basis_rule: ValueBasisRule
    reason:           str


def months_between(start: date, end: date) -> int:
    """
    Whole calendar months between two dates.

    Uses the same convention as lenders: if the end day >= start day,
    count that month as complete.
    """
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def check_seasoning(
    acquisition_date: date,
    lender_profile: dict,
    now: date | None = None,
) -> SeasoningResult:
    """
    Determine if a deal meets the lender's seasoning requirements.

    lender_profile keys used:
        min_seasoning_months        int  — minimum to use cost_basis_value rule
        full_value_seasoning_months int  — months to use full ARV
        early_rule                  str  — 'cost_basis_value' or omit

    Returns SeasoningResult with:
        eligible:         True if months_owned ≥ min_seasoning_months
        value_basis_rule: 'full_value' | 'cost_basis_value' | 'ineligible'
        months_owned:     months from acquisition_date to today
        reason:           human-readable explanation
    """
    today        = now or datetime.now(tz=timezone.utc).date()
    months_owned = months_between(acquisition_date, today)

    full_seasoning = int(lender_profile.get("full_value_seasoning_months", 6))
    min_seasoning  = int(lender_profile.get("min_seasoning_months", 3))
    early_rule     = lender_profile.get("early_rule", "")

    if months_owned >= full_seasoning:
        return SeasoningResult(
            eligible=True,
            months_owned=months_owned,
            value_basis_rule="full_value",
            reason=f"Owned {months_owned}m ≥ {full_seasoning}m full-value seasoning",
        )

    if months_owned >= min_seasoning and early_rule == "cost_basis_value":
        return SeasoningResult(
            eligible=True,
            months_owned=months_owned,
            value_basis_rule="cost_basis_value",
            reason=(
                f"Owned {months_owned}m ≥ {min_seasoning}m minimum; "
                f"value basis = min(ARV, cost_basis)"
            ),
        )

    months_needed = min_seasoning - months_owned
    return SeasoningResult(
        eligible=False,
        months_owned=months_owned,
        value_basis_rule="ineligible",
        reason=(
            f"Only owned {months_owned}m; need {min_seasoning}m minimum "
            f"({months_needed}m remaining)"
        ),
    )
