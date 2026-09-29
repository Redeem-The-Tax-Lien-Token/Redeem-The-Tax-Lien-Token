"""
Agent 8 — Offer & Contract (FastAPI service).

Endpoints:
  GET  /gate-a/queue          — list all Gate A pending items
  GET  /gate-a/{lead_id}      — single Gate A packet (builds contract preview + memo)
  POST /gate-a/{lead_id}/approve  — approve and send offer SMS + e-sign
  POST /gate-a/{lead_id}/reject   — reject with reason → lead → nurture

Contract (§7, Agent 8):
  - Gate A packet: comps, rent comps, both cases, decision memo, contract preview,
    risk flags.
  - On approval: offer SMS sent via build_seller_sms() (disclosure by code),
    PSA sent for e-signature via adapters/esign.py.
  - A gate never times out. Silence = no action.
  - SYSTEM_MODE=dry_run → all SMS and e-sign calls go to log, not live.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Allow imports from monorepo root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from shared.db import session_ctx, log_agent_event
from shared.compliance.disclosures import build_seller_sms

from .gate_a_queue import fetch_gate_a_queue, fetch_gate_a_item, record_gate_a_decision
from .gate_a_packet import build_gate_a_packet
from .contract_builder import build_psa

import yaml as _yaml

log = logging.getLogger(__name__)

_SYSTEM_MODE  = os.environ.get("SYSTEM_MODE", "live")
_OPERATOR_PHONE = os.environ.get("OPERATOR_PHONE", "")
_TWILIO_SID   = os.environ.get("TWILIO_ACCOUNT_SID", "")
_TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
_TWILIO_MSID  = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "")

_CFG_PATH = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"


def _load_cfg() -> dict:
    return _yaml.safe_load(_CFG_PATH.read_text())


app = FastAPI(title="Agent 8 — Offer & Contract", version="0.1.0")


# ── SMS ────────────────────────────────────────────────────────────────────────

def _send_sms(to: str, body: str) -> None:
    if _SYSTEM_MODE == "dry_run":
        log.info("[DRY-RUN] SMS to %s: %s", to, body[:80])
        return
    if not all([_TWILIO_SID, _TWILIO_TOKEN, _TWILIO_MSID, to]):
        log.warning("SMS skipped — Twilio env vars missing")
        return
    try:
        from twilio.rest import Client
        client = Client(_TWILIO_SID, _TWILIO_TOKEN)
        client.messages.create(
            messaging_service_sid=_TWILIO_MSID,
            to=to,
            body=body,
        )
        log.info("SMS sent to %s", to)
    except Exception as exc:
        log.error("SMS send failed: %s", exc)


def _send_offer_sms(
    db: Session,
    lead_id: int,
    phone: str,
    offer_amount: float,
    seller_name: str,
    property_address: str,
) -> str | None:
    """Build and send the offer SMS. Returns disclosure_version for audit."""
    body = (
        f"Hi {seller_name.split()[0] if seller_name else 'there'}, "
        f"we'd like to offer ${offer_amount:,.0f} cash for your property at "
        f"{property_address}. Reply YES to receive the purchase agreement, "
        f"or call us to discuss."
    )
    full_msg, disclosure_version = build_seller_sms(body)
    _send_sms(phone, full_msg)
    # Log to outreach_log for audit
    db.execute(
        __import__("sqlalchemy").text("""
            INSERT INTO outreach_log
                (lead_id, message, channel, direction, status, disclosure_version, sent_at)
            VALUES
                (:lead_id, :msg, 'sms', 'outbound', 'sent', :version, NOW())
        """),
        {"lead_id": lead_id, "msg": full_msg, "version": disclosure_version},
    )
    return disclosure_version


# ── API models ─────────────────────────────────────────────────────────────────

class ApproveRequest(BaseModel):
    approved_by: str = Field(..., description="'operator' or a user identifier")
    send_offer_sms: bool = Field(True, description="Send offer SMS to seller immediately")
    send_for_esign: bool = Field(True, description="Send PSA for e-signature immediately")


class RejectRequest(BaseModel):
    approved_by: str = Field(..., description="'operator' or a user identifier")
    reason: str = Field(..., description="Reason for rejection (goes to lead.notes and nurture)")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/gate-a/queue")
def get_gate_a_queue():
    with session_ctx() as db:
        items = fetch_gate_a_queue(db)
    return {"count": len(items), "items": items}


@app.get("/gate-a/{lead_id}")
def get_gate_a_packet(lead_id: int):
    cfg = _load_cfg()
    with session_ctx() as db:
        item = fetch_gate_a_item(db, lead_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Lead not in Gate A queue")
    use_llm = (_SYSTEM_MODE != "dry_run")
    packet = build_gate_a_packet(item=item, cfg=cfg, use_llm=use_llm)
    return packet.to_dict()


@app.post("/gate-a/{lead_id}/approve")
def approve_gate_a(lead_id: int, body: ApproveRequest):
    """
    Approve Gate A — validates decision, sends offer SMS + e-sign, updates DB.

    A gate never auto-approves. This endpoint is the only path to approval.
    """
    cfg = _load_cfg()

    with session_ctx() as db:
        item = fetch_gate_a_item(db, lead_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Lead not in Gate A queue")

        deal_id      = item["deal_id"]
        offer_amount = float(item.get("offer_amount") or 0)
        phone        = item.get("phone") or ""
        seller_name  = item.get("seller_name") or "Seller"
        address      = item.get("address") or ""

        disclosure_version = None
        esign_envelope_id  = None

        # Send offer SMS (disclosure inserted by build_seller_sms, never LLM)
        if body.send_offer_sms and phone:
            disclosure_version = _send_offer_sms(
                db, lead_id, phone, offer_amount, seller_name, address,
            )

        # Send PSA for e-signature
        if body.send_for_esign and deal_id:
            from datetime import date
            import adapters.esign as esign

            closing_date_raw = item.get("closing_date")
            if isinstance(closing_date_raw, str):
                closing_date = date.fromisoformat(closing_date_raw)
            elif isinstance(closing_date_raw, date):
                closing_date = closing_date_raw
            else:
                closing_date = date.today()

            strategy = item.get("strategy") or "wholesale"
            psa_text, psa_disc_version = build_psa(
                seller_name=seller_name,
                property_address=address,
                property_city=item.get("city") or "",
                property_state=item.get("state") or "IN",
                property_zip=item.get("zip") or "",
                attom_id=None,
                purchase_price=int(offer_amount),
                emd_amount=int(
                    item.get("emd_amount")
                    or cfg.get("contract", {}).get("emd_amount", 1000)
                ),
                inspection_period_days=int(
                    item.get("inspection_period_days")
                    or cfg.get("contract", {}).get(
                        "inspection_period_days", {}
                    ).get(strategy, 10)
                ),
                closing_date=closing_date,
                cfg=cfg,
            )
            esign_envelope_id = esign.send_for_signature(
                to_email=item.get("email") or "",
                to_name=seller_name,
                subject=f"Purchase Agreement — {address}",
                document_bytes=psa_text.encode("utf-8"),
                document_name="Purchase_Agreement.pdf",
                idempotency_key=f"deal-{deal_id}",
            )
            db.execute(
                __import__("sqlalchemy").text("""
                    UPDATE deals
                    SET esign_envelope_id = :eid, updated_at = NOW()
                    WHERE id = :did
                """),
                {"eid": esign_envelope_id, "did": deal_id},
            )

        # Stamp Gate A approval
        record_gate_a_decision(db, deal_id, approved=True, approved_by=body.approved_by)

        # Transition lead status → offer_sent
        db.execute(
            __import__("sqlalchemy").text("""
                UPDATE leads SET status = 'offer_sent', updated_at = NOW()
                WHERE id = :lid AND status IN ('offer_ready', 'strategy_switch')
            """),
            {"lid": lead_id},
        )

        log_agent_event(
            source_agent="offer-contract",
            target_agent="operator",
            payload={
                "event_type":          "GATE_A_APPROVED",
                "lead_id":             lead_id,
                "deal_id":             deal_id,
                "approved_by":         body.approved_by,
                "offer_amount":        offer_amount,
                "esign_envelope_id":   esign_envelope_id,
                "disclosure_version":  disclosure_version,
            },
            response={},
            status_code=200,
        )
        log.info("Gate A approved: lead #%d, deal #%d", lead_id, deal_id)

    return {
        "status":            "approved",
        "lead_id":           lead_id,
        "deal_id":           deal_id,
        "esign_envelope_id": esign_envelope_id,
        "offer_sent":        body.send_offer_sms,
    }


@app.post("/gate-a/{lead_id}/reject")
def reject_gate_a(lead_id: int, body: RejectRequest):
    """
    Reject Gate A — lead goes to nurture, reason recorded on the deal.
    A gate never times out. This endpoint is the only path to rejection.
    """
    with session_ctx() as db:
        item = fetch_gate_a_item(db, lead_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Lead not in Gate A queue")

        deal_id = item["deal_id"]
        record_gate_a_decision(
            db, deal_id, approved=False,
            approved_by=body.approved_by, reason=body.reason,
        )
        db.execute(
            __import__("sqlalchemy").text("""
                UPDATE leads SET status = 'nurture', updated_at = NOW()
                WHERE id = :lid AND status IN ('offer_ready', 'strategy_switch')
            """),
            {"lid": lead_id},
        )
        log_agent_event(
            source_agent="offer-contract",
            target_agent="operator",
            payload={
                "event_type":  "GATE_A_REJECTED",
                "lead_id":     lead_id,
                "deal_id":     deal_id,
                "approved_by": body.approved_by,
                "reason":      body.reason,
            },
            response={},
            status_code=200,
        )
        log.info("Gate A rejected: lead #%d → nurture", lead_id)

    return {"status": "rejected", "lead_id": lead_id, "new_lead_status": "nurture"}


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    import uvicorn
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [offer-contract] %(message)s",
    )
    uvicorn.run(
        "agents.offer_contract.run:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8008")),
        reload=False,
    )


if __name__ == "__main__":
    main()
