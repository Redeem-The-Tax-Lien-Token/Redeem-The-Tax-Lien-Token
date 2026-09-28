"""
Lender adapter (stub) — vendor-neutral interface for acquisition and refi lenders.

In production, swap the stub for real lender-portal API calls or a manual-data
entry flow.  The interface is stable; the implementation is swappable per
CLAUDE.md §4 principle 8.

Public API:
    submit_application(lender_id, deal_context) -> ApplicationResult
    check_application_status(lender_id, application_id) -> ApplicationStatus
    fetch_term_sheet(lender_id, application_id) -> TermSheet | None
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_SYSTEM_MODE = os.environ.get("SYSTEM_MODE", "live")


@dataclass(frozen=True)
class ApplicationResult:
    application_id: str
    lender_id:      str
    status:         str   # 'submitted' | 'pending' | 'approved' | 'declined' | 'dry_run'
    notes:          str = ""


@dataclass(frozen=True)
class ApplicationStatus:
    application_id: str
    status:         str
    notes:          str = ""


@dataclass(frozen=True)
class TermSheet:
    lender_id:       str
    application_id:  str
    loan_amount:     float
    rate:            float
    points:          float
    term_months:     int
    fixed_fees:      float
    conditions:      list[str] = field(default_factory=list)


def submit_application(lender_id: str, deal_context: dict) -> ApplicationResult:
    """Submit a loan application to the named lender."""
    if _SYSTEM_MODE == "dry_run":
        log.info(
            "[DRY-RUN][lender] submit_application lender=%s deal=%s",
            lender_id, deal_context.get("deal_id"),
        )
        return ApplicationResult(
            application_id=f"dry_run_{lender_id}",
            lender_id=lender_id,
            status="dry_run",
            notes="Dry-run mode — no real application submitted",
        )
    raise NotImplementedError(
        "Lender adapter is a stub. Wire in a real lender portal or manual-entry "
        "flow before going live. See adapters/lender.py."
    )


def check_application_status(lender_id: str, application_id: str) -> ApplicationStatus:
    """Check the status of a previously submitted application."""
    if _SYSTEM_MODE == "dry_run":
        return ApplicationStatus(application_id=application_id, status="dry_run")
    raise NotImplementedError("Lender adapter is a stub.")


def fetch_term_sheet(lender_id: str, application_id: str) -> TermSheet | None:
    """Fetch a term sheet once the lender has approved the application."""
    if _SYSTEM_MODE == "dry_run":
        return None
    raise NotImplementedError("Lender adapter is a stub.")
