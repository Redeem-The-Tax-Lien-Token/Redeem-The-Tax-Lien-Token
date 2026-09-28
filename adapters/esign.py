"""
E-sign adapter stub (§4 principle 8).

Vendor not yet selected. The interface is stable: contract code calls
send_for_signature() and check_status() — the implementation behind it
will be swapped once DocuSign / PandaDoc / HelloSign is chosen.

All methods in dry-run mode write to the log and return dummy values.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

_SYSTEM_MODE = os.environ.get("SYSTEM_MODE", "live")


@dataclass
class EnvelopeStatus:
    envelope_id: str
    status: str          # "sent" | "delivered" | "completed" | "voided" | "stub"
    signed_at: str | None = None
    signer_email: str | None = None


def send_for_signature(
    *,
    to_email: str,
    to_name: str,
    subject: str,
    document_bytes: bytes,
    document_name: str = "Purchase_Agreement.pdf",
    idempotency_key: str,
) -> str:
    """
    Send a document for e-signature.

    Returns an envelope_id string that can be stored on deals.esign_envelope_id
    and later polled with check_status().

    In dry-run mode: logs and returns a stub ID.
    In live mode: raises NotImplementedError until a vendor is wired up.
    """
    if _SYSTEM_MODE == "dry_run":
        stub_id = f"stub-{idempotency_key[:16]}"
        log.info(
            "[DRY-RUN] e-sign: would send '%s' to %s <%s>; envelope_id=%s",
            document_name, to_name, to_email, stub_id,
        )
        return stub_id

    raise NotImplementedError(
        "E-sign vendor not yet configured. "
        "Set SYSTEM_MODE=dry_run or wire up an e-sign vendor in adapters/esign.py."
    )


def check_status(envelope_id: str) -> EnvelopeStatus:
    """
    Return the current status of an envelope.

    In dry-run mode: returns a stub status.
    In live mode: raises NotImplementedError until a vendor is wired up.
    """
    if _SYSTEM_MODE == "dry_run":
        return EnvelopeStatus(
            envelope_id=envelope_id,
            status="stub",
        )

    raise NotImplementedError(
        "E-sign vendor not yet configured. "
        "Set SYSTEM_MODE=dry_run or wire up an e-sign vendor in adapters/esign.py."
    )
