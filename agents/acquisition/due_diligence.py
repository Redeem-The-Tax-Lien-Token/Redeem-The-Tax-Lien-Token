"""
Due-diligence checklist logic for Agent 12.

A fixed list of checks must pass (or be waived by the operator) before the
deal can advance to FUNDING_SECURED.  All evaluation is deterministic Python.

Public API:
    DD_CHECKS            — ordered list of all check names
    initial_dd_rows(deal_id) -> list[dict]  — rows to INSERT at deal start
    evaluate_dd_status(rows) -> DDStatus    — all-pass / any-fail / pending
    can_advance_to_funding(rows) -> bool    — True only when all checks pass or waived
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Ordered checklist — every BRRRR deal must clear these before funding
DD_CHECKS: list[str] = [
    "title_search_reviewed",        # title search ordered and reviewed for liens/clouds
    "inspection_scheduled",         # physical inspection on calendar
    "inspection_complete",          # inspection report received
    "insurance_quote_received",     # hazard insurance quote in hand
    "tax_check",                    # current tax status and delinquencies verified
    "code_violations_check",        # city code violation search complete
    "utilities_verified",           # water/sewer/gas/electric accounts confirmed
    "hoa_check",                    # HOA (if any) dues and rules confirmed
]

# Checks that can be waived by the operator without a fail outcome
WAIVABLE_CHECKS: frozenset[str] = frozenset({"hoa_check"})

StatusLiteral = Literal["all_pass", "has_fail", "pending"]


@dataclass(frozen=True)
class DDStatus:
    outcome:        StatusLiteral
    failed_checks:  list[str]
    pending_checks: list[str]


def initial_dd_rows(deal_id: int) -> list[dict]:
    """Return INSERT-ready rows for a new deal's DD checklist."""
    return [
        {"deal_id": deal_id, "check_name": name, "status": "pending"}
        for name in DD_CHECKS
    ]


def evaluate_dd_status(rows: list[dict]) -> DDStatus:
    """
    Summarise the DD checklist.

    rows: list of dicts with keys: check_name, status
          status ∈ {'pending', 'pass', 'fail', 'waived'}
    """
    failed  = [r["check_name"] for r in rows if r["status"] == "fail"]
    pending = [r["check_name"] for r in rows if r["status"] == "pending"]

    if failed:
        return DDStatus(outcome="has_fail", failed_checks=failed, pending_checks=pending)
    if pending:
        return DDStatus(outcome="pending", failed_checks=[], pending_checks=pending)
    return DDStatus(outcome="all_pass", failed_checks=[], pending_checks=[])


def can_advance_to_funding(rows: list[dict]) -> bool:
    """
    True only when every check is either 'pass' or 'waived'.
    A single 'fail' or 'pending' blocks advancement — fail closed.
    """
    for row in rows:
        if row["status"] not in ("pass", "waived"):
            return False
    return bool(rows)  # empty checklist never advances
