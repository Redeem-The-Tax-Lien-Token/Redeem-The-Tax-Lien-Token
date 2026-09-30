"""
Payments adapter (stub) — vendor-neutral interface for all money movements.

Covers: EMD (earnest money deposit), assignment proceeds receipt, contractor
draw disbursements, and any other deal-level payment.

GATE RULES (CLAUDE.md §1): every payment that moves money or opens/changes
title requires Gate B approval before submission.  This adapter MUST only
be called after a Gate B approval record exists on the deal.  The caller
is responsible for verifying gate approval; this adapter does not re-check.

FAIL-CLOSED: any uncertainty about gate approval, payee identity, or amount
must block the payment.  Never retry a payment without confirming the first
attempt did not process.  Use the idempotency_key to prevent double sends.

Public API:
    submit_payment(...)      -> PaymentResult
    check_payment_status(...)-> PaymentStatus
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_SYSTEM_MODE = os.environ.get("SYSTEM_MODE", "live")

# Allowed payment types (deterministic — never inferred by LLM)
PAYMENT_TYPES = frozenset({
    "emd",              # earnest money deposit
    "emd_return",       # EMD returned to buyer on deal fall-through
    "assignment_fee",   # assignment proceeds from end buyer
    "draw",             # contractor draw disbursement
    "closing_cost",     # closing-related disbursement
    "refi_cost",        # refinance closing cost
    "other",            # operator-initiated one-off (rare)
})


@dataclass(frozen=True)
class PaymentResult:
    payment_id:      str
    status:          str   # 'submitted' | 'pending' | 'cleared' | 'failed' | 'dry_run'
    amount:          float
    payment_type:    str
    deal_id:         int
    idempotency_key: str
    notes:           str = ""


@dataclass(frozen=True)
class PaymentStatus:
    payment_id: str
    status:     str
    notes:      str = ""


def submit_payment(
    *,
    payment_type:    str,
    amount:          float,
    deal_id:         int,
    payee_name:      str,
    payee_account:   str,   # wire routing + account, check payable, or ACH
    memo:            str,
    idempotency_key: str,
) -> PaymentResult:
    """
    Submit a payment.

    Gate B approval must exist before calling this.  The caller is responsible
    for verifying approval; this adapter does not re-check.

    In dry-run mode: logs the intent and returns a stub result.
    In live mode: raises NotImplementedError until a payment processor is wired.
    """
    if payment_type not in PAYMENT_TYPES:
        raise ValueError(
            f"Unknown payment_type '{payment_type}'. "
            f"Allowed: {sorted(PAYMENT_TYPES)}"
        )
    if amount <= 0:
        raise ValueError("Payment amount must be positive")

    if _SYSTEM_MODE == "dry_run":
        log.info(
            "[DRY-RUN][payments] submit_payment type=%s amount=%.2f deal=%d "
            "payee=%s key=%s",
            payment_type, amount, deal_id, payee_name, idempotency_key[:16],
        )
        return PaymentResult(
            payment_id      = f"dry_run_{idempotency_key[:16]}",
            status          = "dry_run",
            amount          = amount,
            payment_type    = payment_type,
            deal_id         = deal_id,
            idempotency_key = idempotency_key,
            notes           = "Dry-run mode — no real payment submitted",
        )

    raise NotImplementedError(
        "Payments adapter is a stub. Wire in a real payment processor "
        "(e.g., Dwolla, Plaid, or bank ACH API) in adapters/payments.py "
        "before going live."
    )


def check_payment_status(payment_id: str) -> PaymentStatus:
    """Return the current status of a submitted payment."""
    if _SYSTEM_MODE == "dry_run":
        return PaymentStatus(payment_id=payment_id, status="dry_run")
    raise NotImplementedError("Payments adapter is a stub.")
