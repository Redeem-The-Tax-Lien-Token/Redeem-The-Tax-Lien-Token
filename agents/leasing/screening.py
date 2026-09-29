"""
Tenant screening logic for Agent 14.

All pass/fail decisions are deterministic Python applied from SCREENING_CRITERIA
— the LLM is never involved.  Written, uniform criteria are applied identically
to every applicant (FHA §804).

Public API:
    CRITERIA_VERSION            — hash/version of the criteria in use
    ScreeningDecision           — dataclass
    evaluate_application(application, monthly_rent, criteria=None) -> ScreeningDecision
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from adapters.tenant_screening import SCREENING_CRITERIA
from .fair_housing import guard_application_fields

# Version tracks which criteria were applied — stored on each application row.
CRITERIA_VERSION = "1.0"


@dataclass(frozen=True)
class ScreeningDecision:
    decision:        str   # 'approved' | 'denied' | 'pending'
    reasons:         list[str] = field(default_factory=list)
    criteria_version: str = CRITERIA_VERSION


def evaluate_application(
    application: dict,
    monthly_rent: float,
    criteria: dict | None = None,
) -> ScreeningDecision:
    """
    Apply uniform screening criteria to a single application.

    application keys (objective criteria only — no protected characteristics):
        monthly_income  float   — gross monthly income
        credit_score    int     — credit score (required)
        debt_to_income  float   — current DTI as decimal (e.g. 0.35 = 35%)
        eviction_history bool   — any eviction in the lookback period
        prior_landlord_reference bool — reference available
        # criminal_record: per HUD guidance, individualized assessment required;
        # a blanket criminal disqualification may violate FHA. Agent 14 flags
        # applications with criminal records for human review rather than
        # auto-denying. The LLM is not used; a human makes the decision.

    monthly_rent: the listed rent for this unit.
    criteria: override dict (for testing); defaults to SCREENING_CRITERIA.
    """
    guard_application_fields(application)

    c       = criteria or SCREENING_CRITERIA
    reasons: list[str] = []
    pending = False

    income = float(application.get("monthly_income") or 0)
    min_income = monthly_rent * float(c.get("min_income_multiple_of_rent", 3.0))
    if income < min_income:
        reasons.append(
            f"Monthly income ${income:,.0f} below minimum ${min_income:,.0f} "
            f"({c.get('min_income_multiple_of_rent', 3.0)}× rent)"
        )

    credit = application.get("credit_score")
    if credit is not None:
        if float(credit) < float(c.get("min_credit_score", 580)):
            reasons.append(
                f"Credit score {credit} below minimum {c.get('min_credit_score', 580)}"
            )
    else:
        pending = True   # credit score not yet available

    dti = application.get("debt_to_income")
    if dti is not None and float(dti) > float(c.get("max_debt_to_income_pct", 0.45)):
        reasons.append(
            f"Debt-to-income {dti:.0%} exceeds maximum "
            f"{c.get('max_debt_to_income_pct', 0.45):.0%}"
        )

    if application.get("eviction_history"):
        reasons.append(
            f"Eviction on record within the last "
            f"{c.get('eviction_history_years', 7)} years"
        )

    if c.get("prior_landlord_reference_required") and \
            not application.get("prior_landlord_reference"):
        reasons.append("Prior landlord reference required but not provided")

    if pending:
        return ScreeningDecision(decision="pending", reasons=reasons)
    if reasons:
        return ScreeningDecision(decision="denied", reasons=reasons)
    return ScreeningDecision(decision="approved", reasons=[])
