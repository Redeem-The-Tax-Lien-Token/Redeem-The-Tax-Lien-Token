"""
Tenant screening adapter (stub) — Fair Housing Act compliant.

CRITICAL COMPLIANCE REQUIREMENT:
  Screening criteria must be written, objective, and applied identically to
  every applicant.  Protected characteristics (race, color, national origin,
  religion, sex, familial status, disability, and Indiana additions) must
  NEVER appear in evaluation logic, criteria, or results.

  The LLM is NEVER involved in applicant evaluation.  All pass/fail decisions
  are made by this module from objective, numeric criteria only.

Public API:
    SCREENING_CRITERIA          — the uniform criteria dict (read-only)
    screen_applicant(application) -> ScreeningResult
    check_status(screening_id) -> ScreeningResult
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_SYSTEM_MODE = os.environ.get("SYSTEM_MODE", "live")

# Uniform written criteria — applied identically to every applicant (FHA §804).
# These are objective financial and rental history criteria only.
# NEVER add criteria related to: race, color, national origin, religion, sex,
# familial status, disability, source of income (Indiana), sexual orientation
# (Indianapolis local ordinance), gender identity.
SCREENING_CRITERIA: dict = {
    "min_income_multiple_of_rent": 3.0,   # gross monthly income ≥ 3× monthly rent
    "min_credit_score": 580,
    "max_debt_to_income_pct": 0.45,
    "eviction_history_years": 7,           # eviction in last 7 years → disqualified
    "felony_lookback_years": 7,            # only crimes directly related to tenancy safety
    "criminal_individualized_assessment": True,  # required by HUD guidance — per-case review
    "prior_landlord_reference_required": True,
}

# Protected characteristics — never evaluated, never logged, never passed to LLM
_PROTECTED_CHARACTERISTICS = frozenset({
    "race", "color", "national_origin", "religion", "sex",
    "familial_status", "disability", "source_of_income",
    "sexual_orientation", "gender_identity",
})


@dataclass(frozen=True)
class ScreeningResult:
    screening_id: str
    status:       str   # 'pass' | 'fail' | 'pending' | 'dry_run'
    reasons:      list[str] = field(default_factory=list)
    notes:        str  = ""


def screen_applicant(application: dict) -> ScreeningResult:
    """
    Submit a screening request.

    application must NOT contain protected characteristics.  This function
    raises ValueError if any protected field is present — fail closed.
    """
    for key in application:
        if key.lower() in _PROTECTED_CHARACTERISTICS:
            raise ValueError(
                f"Application contains protected characteristic field '{key}'. "
                "This field must not be collected or passed to screening. "
                "Review intake form for FHA compliance."
            )

    if _SYSTEM_MODE == "dry_run":
        log.info(
            "[DRY-RUN][screening] screen_applicant deal=%s",
            application.get("deal_id"),
        )
        return ScreeningResult(
            screening_id=f"dry_run_{application.get('applicant_id', 0)}",
            status="dry_run",
            notes="Dry-run mode — no real screening submitted",
        )
    raise NotImplementedError(
        "Tenant screening adapter is a stub. Wire in a real screening service "
        "(e.g., TransUnion SmartMove, RentSpree) before going live."
    )


def check_status(screening_id: str) -> ScreeningResult:
    if _SYSTEM_MODE == "dry_run":
        return ScreeningResult(screening_id=screening_id, status="dry_run")
    raise NotImplementedError("Tenant screening adapter is a stub.")
