"""
Accounting export adapter (stub) — vendor-neutral interface.

Exports deal transactions and portfolio performance data to an external
accounting system (QuickBooks Online, Xero, Wave, etc.).

Secrets: NEVER log amounts with PII (tenant SSN, bank account) in the same
record.  Each transaction carries a deal_id for linkage; the accounting system
receives only what is needed for double-entry bookkeeping.

Public API:
    export_transaction(...)  -> ExportResult
    export_rent_roll(...)    -> ExportResult
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

_SYSTEM_MODE = os.environ.get("SYSTEM_MODE", "live")

# Allowed transaction types (deterministic — never inferred by LLM)
TRANSACTION_TYPES = frozenset({
    "acquisition",       # purchase closing
    "rehab_draw",        # contractor draw
    "assignment_income", # wholesale fee received
    "rent_income",       # monthly rent payment received
    "operating_expense", # taxes, insurance, maintenance, utilities
    "refi_proceeds",     # cash-out refinance proceeds
    "refi_costs",        # refi closing costs
    "capex_reserve",     # capital reserve contribution
    "other",
})


@dataclass(frozen=True)
class ExportResult:
    export_id:        str
    status:           str    # 'exported' | 'pending' | 'dry_run'
    transaction_type: str
    deal_id:          int
    amount:           float
    notes:            str = ""


def export_transaction(
    *,
    deal_id:          int,
    transaction_type: str,
    amount:           float,
    transaction_date: str,   # ISO date "YYYY-MM-DD"
    memo:             str,
    idempotency_key:  str,
) -> ExportResult:
    """
    Export a single transaction to the accounting system.

    In dry-run mode: logs and returns a stub.
    In live mode: raises NotImplementedError until a vendor is wired.
    """
    if transaction_type not in TRANSACTION_TYPES:
        raise ValueError(
            f"Unknown transaction_type '{transaction_type}'. "
            f"Allowed: {sorted(TRANSACTION_TYPES)}"
        )

    if _SYSTEM_MODE == "dry_run":
        log.info(
            "[DRY-RUN][accounting] export_transaction type=%s amount=%.2f "
            "deal=%d date=%s",
            transaction_type, amount, deal_id, transaction_date,
        )
        return ExportResult(
            export_id        = f"dry_run_{idempotency_key[:16]}",
            status           = "dry_run",
            transaction_type = transaction_type,
            deal_id          = deal_id,
            amount           = amount,
            notes            = "Dry-run mode — not exported to accounting system",
        )

    raise NotImplementedError(
        "Accounting export adapter is a stub. Wire in a real accounting system "
        "(QuickBooks Online, Xero, Wave) in adapters/accounting_export.py "
        "before going live."
    )


def export_rent_roll(
    *,
    period:          str,    # "YYYY-MM"
    rent_roll_rows:  list[dict],
    idempotency_key: str,
) -> ExportResult:
    """
    Export the monthly rent roll to the accounting system.

    rent_roll_rows: list of dicts with keys: deal_id, address, tenant_name,
                    monthly_rent, amount_paid, status.
    NEVER include SSN, bank account, or other PII in rent_roll_rows.
    """
    if _SYSTEM_MODE == "dry_run":
        log.info(
            "[DRY-RUN][accounting] export_rent_roll period=%s rows=%d",
            period, len(rent_roll_rows),
        )
        total = sum(r.get("amount_paid", 0) for r in rent_roll_rows)
        return ExportResult(
            export_id        = f"dry_run_{idempotency_key[:16]}",
            status           = "dry_run",
            transaction_type = "rent_income",
            deal_id          = 0,
            amount           = total,
            notes            = f"Dry-run mode — {len(rent_roll_rows)} rows not exported",
        )

    raise NotImplementedError("Accounting export adapter is a stub.")
