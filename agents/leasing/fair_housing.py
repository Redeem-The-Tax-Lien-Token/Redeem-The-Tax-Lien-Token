"""
Fair Housing Act compliance module for Agent 14.

Provides:
  1. Listing text linter — flags language that implies preference, limitation,
     or discrimination against protected classes.
  2. Applicant evaluation guard — raises on any protected-characteristic field.

All evaluation of applicants is done by deterministic Python criteria only.
The LLM is NEVER involved in deciding whether an applicant qualifies.

Protected classes (federal FHA + Indiana additions):
  Race, color, national origin, religion, sex, familial status, disability,
  source of income (Indiana), sexual orientation (Indianapolis city ordinance),
  gender identity.

Public API:
    FairHousingLintResult(ok, flags)
    lint_listing(text) -> FairHousingLintResult
    PROTECTED_CLASSES               — frozenset of class names
    guard_application_fields(fields) — raises ValueError on protected field
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PROTECTED_CLASSES: frozenset[str] = frozenset({
    "race",
    "color",
    "national_origin",
    "religion",
    "sex",
    "familial_status",
    "disability",
    "source_of_income",
    "sexual_orientation",
    "gender_identity",
})

# Patterns that may indicate FHA-violating language in listing text.
# Each tuple: (regex pattern, explanation)
_FLAGGED_PATTERNS: list[tuple[str, str]] = [
    # Familial status / children
    (r"\bno children\b", "implies restriction on familial status"),
    (r"\badults only\b", "implies restriction on familial status"),
    (r"\bchildless\b", "implies restriction on familial status"),
    (r"\bno kids\b", "implies restriction on familial status"),
    # National origin / religion
    (r"\bchristian[s]?\b", "may imply religious preference"),
    (r"\bno section 8\b", "source-of-income restriction (Indiana law)"),
    (r"\bno (housing )?vouchers?\b", "source-of-income restriction"),
    (r"\bnot accept(ing)? (housing )?vouchers?\b", "source-of-income restriction"),
    (r"\bno hud\b", "source-of-income restriction"),
    # Disability
    (r"\bno (wheelchairs?|disabled|handicapped)\b", "disability restriction"),
    # Gender / sex
    (r"\b(males?|females?) only\b", "sex restriction"),
    # Neighborhood steering (do not describe neighborhood by demographic)
    (r"\b(quiet|safe|good) (neighborhood|community|area) for (families|professionals|christians)\b",
     "potential steering language"),
]


@dataclass(frozen=True)
class FairHousingLintResult:
    ok:    bool
    flags: list[str] = field(default_factory=list)


def lint_listing(text: str) -> FairHousingLintResult:
    """
    Check listing text for Fair Housing Act violations.

    Returns FairHousingLintResult.ok=False if any flagged pattern is found.
    Listing must not be sent until this passes.
    """
    flags: list[str] = []
    lower = text.lower()

    for pattern, explanation in _FLAGGED_PATTERNS:
        if re.search(pattern, lower):
            flags.append(f"Pattern '{pattern}': {explanation}")

    return FairHousingLintResult(ok=not flags, flags=flags)


def guard_application_fields(fields: dict) -> None:
    """
    Raise ValueError if the application dict contains any protected-class field.

    Call this before passing any application data anywhere — the LLM, the
    screening adapter, or the database.
    """
    _PROTECTED_FIELD_NAMES = frozenset({
        "race", "color", "national_origin", "nationality", "ethnicity",
        "religion", "sex", "gender", "familial_status", "children",
        "disability", "handicap", "source_of_income", "voucher",
        "sexual_orientation", "gender_identity",
    })
    found = [k for k in fields if k.lower() in _PROTECTED_FIELD_NAMES]
    if found:
        raise ValueError(
            f"Application contains protected characteristic fields: {found}. "
            "Remove these fields before processing. FHA §804 compliance required."
        )
