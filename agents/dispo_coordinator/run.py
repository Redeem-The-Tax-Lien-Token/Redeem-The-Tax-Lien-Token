"""
Agent 11 — Dispo & Closing Coordinator (FastAPI service).

Wholesale track from buyer selection through fee receipt.

Endpoints:
  POST /buyer-offer/{deal_id}          — record a buyer's offer + qualification
  POST /buyer-offer/{deal_id}/select   — select the best buyer, advance to buyer_selected
  GET  /gate-b/queue                   — list all Gate B pending deals
  GET  /gate-b/{deal_id}               — full Gate B packet
  POST /gate-b/{deal_id}/approve       — approve → send assignment e-sign, advance to assigned
  POST /gate-b/{deal_id}/reject        — reject → return to in_dispo

  POST /deal/{deal_id}/title-open      — record title company, advance to title_open_w
  POST /deal/{deal_id}/clear-to-close  — advance to clear_to_close_w
  POST /deal/{deal_id}/closed          — record closing, advance to closed_w
  POST /deal/{deal_id}/fee-received    — record fee receipt → fee_received (terminal)

Contract (§7, Agent 11):
  - A gate never times out. Silence = no action.
  - Buyer-facing messages carry the assignment disclosure (§2.1) inserted by code.
  - SYSTEM_MODE=dry_run → all SMS and e-sign calls go to log only.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from shared.db import session_ctx, log_agent_event
from shared.state_machine import transition_deal

from .gate_b_queue import (
    fetch_gate_b_queue, fetch_gate_b_item, fetch_buyer_offers,
    record_buyer_offer, select_buyer, record_gate_b_decision,
)
from .gate_b_packet import build_gate_b_packet
from .assignment_builder import build_assignment
from .buyer_qualifier import qualify_buyer

import yaml as _yaml

log = logging.getLogger(__name__)

_SYSTEM_MODE    = os.environ.get("SYSTEM_MODE", "live")
_TWILIO_SID     = os.environ.get("TWILIO_ACCOUNT_SID", "")
_TWILIO_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN", "")
_TWILIO_MSID    = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "")

_CFG_PATH = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"


def _load_cfg() -> dict:
    return _yaml.safe_load(_CFG_PATH.read_text())


app = FastAPI(title="Agent 11 — Dispo & Closing Coordinator", version="0.1.0")


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
        client.messages.create(messaging_service_sid=_TWILIO_MSID, to=to, body=body)
        log.info("SMS sent to %s", to)
    except Exception as exc:
        log.error("SMS send failed: %s", exc)


# ── API models ─────────────────────────────────────────────────────────────────

class BuyerOfferRequest(BaseModel):
    buyer_id:      int
    offer_amount:  float
    pof_confirmed: bool = False
    timeline_days: int | None = None
    emd_capacity:  float | None = None
    notes:         str | None = None


class SelectBuyerRequest(BaseModel):
    buyer_id:       int
    assignment_fee: float


class ApproveGateBRequest(BaseModel):
    approved_by:    str
    send_for_esign: bool = True


class RejectGateBRequest(BaseModel):
    approved_by: str
    reason:      str


class TitleOpenRequest(BaseModel):
    title_company: str
    actor:         str = "dispo-coordinator"


class ClosedRequest(BaseModel):
    actor: str = "dispo-coordinator"


class FeeReceivedRequest(BaseModel):
    amount_received: float
    actor:           str = "dispo-coordinator"


# ── Buyer offer endpoints ──────────────────────────────────────────────────────

@app.post("/buyer-offer/{deal_id}")
def record_offer(deal_id: int, body: BuyerOfferRequest):
    """Record or update a buyer's interest in a deal."""
    cfg = _load_cfg()
    with session_ctx() as db:
        # Quick deal existence check
        row = db.execute(
            text("SELECT id, status, emd_amount FROM deals WHERE id = :id"),
            {"id": deal_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Deal not found")
        if row.status not in ("in_dispo", "buyer_selected"):
            raise HTTPException(
                status_code=409,
                detail=f"Deal is in status '{row.status}' — not accepting buyer offers",
            )
        deal_for_qual = {"emd_amount": row.emd_amount, "offer_amount": row.emd_amount}
        offer_data = {
            "offer_amount": body.offer_amount,
            "pof_confirmed": body.pof_confirmed,
            "timeline_days": body.timeline_days,
            "emd_capacity": body.emd_capacity,
        }
        result = qualify_buyer(offer_data, deal_for_qual, cfg)
        offer_id = record_buyer_offer(
            db, deal_id, body.buyer_id, body.offer_amount,
            body.pof_confirmed, body.timeline_days, body.emd_capacity, body.notes,
        )
        db.execute(
            text("UPDATE buyer_offers SET status = :s WHERE id = :id"),
            {"s": "qualified" if result.qualified else "disqualified", "id": offer_id},
        )
        db.commit()
    return {
        "offer_id":  offer_id,
        "qualified": result.qualified,
        "reasons":   result.reasons,
    }


@app.post("/buyer-offer/{deal_id}/select")
def do_select_buyer(deal_id: int, body: SelectBuyerRequest):
    """Select a buyer and advance the deal to buyer_selected (awaiting Gate B)."""
    with session_ctx() as db:
        row = db.execute(
            text("SELECT id, status, lead_id FROM deals WHERE id = :id"),
            {"id": deal_id},
        ).fetchone()
        if not row or row.status != "in_dispo":
            raise HTTPException(status_code=409, detail="Deal must be in_dispo to select a buyer")
        select_buyer(db, deal_id, body.buyer_id, body.assignment_fee)
        transition_deal(deal_id, "buyer_selected", actor="dispo-coordinator",
                        reason=f"Buyer #{body.buyer_id} selected", db=db)
        log_agent_event(
            source_agent="dispo-coordinator",
            target_agent="orchestrator",
            payload={"event_type": "BUYER_SELECTED", "deal_id": deal_id,
                     "buyer_id": body.buyer_id, "assignment_fee": body.assignment_fee},
            response={}, status_code=202,
        )
    return {"status": "buyer_selected", "deal_id": deal_id, "buyer_id": body.buyer_id}


# ── Gate B endpoints ───────────────────────────────────────────────────────────

@app.get("/gate-b/queue")
def get_gate_b_queue():
    with session_ctx() as db:
        items = fetch_gate_b_queue(db)
    return {"count": len(items), "items": items}


@app.get("/gate-b/{deal_id}")
def get_gate_b_packet(deal_id: int):
    cfg = _load_cfg()
    with session_ctx() as db:
        item = fetch_gate_b_item(db, deal_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Deal not in Gate B queue")
        all_offers = fetch_buyer_offers(db, deal_id)
    packet = build_gate_b_packet(item=item, all_offers=all_offers, cfg=cfg)
    return packet.to_dict()


@app.post("/gate-b/{deal_id}/approve")
def approve_gate_b(deal_id: int, body: ApproveGateBRequest):
    """
    Approve Gate B: send assignment for e-sign, advance deal to assigned.
    A gate never auto-approves — this endpoint is the only approval path.
    """
    cfg = _load_cfg()

    with session_ctx() as db:
        item = fetch_gate_b_item(db, deal_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Deal not in Gate B queue")

        all_offers = fetch_buyer_offers(db, deal_id)
        packet = build_gate_b_packet(item=item, all_offers=all_offers, cfg=cfg)

        esign_id = None
        if body.send_for_esign and packet.selected_buyer:
            import adapters.esign as esign
            assignment_text = packet.assignment_preview
            try:
                esign_id = esign.send_for_signature(
                    to_email=packet.selected_buyer.get("buyer_email") or "",
                    to_name=packet.selected_buyer.get("buyer_name") or "Buyer",
                    subject=f"Assignment Agreement — {packet.property_address}",
                    document_bytes=assignment_text.encode("utf-8"),
                    document_name="Assignment_Agreement.pdf",
                    idempotency_key=f"assignment-{deal_id}",
                )
                db.execute(
                    text("UPDATE deals SET assignment_esign_id = :eid WHERE id = :did"),
                    {"eid": esign_id, "did": deal_id},
                )
            except Exception as exc:
                log.error("E-sign send failed for deal %d: %s", deal_id, exc)

        record_gate_b_decision(db, deal_id, approved=True, approved_by=body.approved_by)
        transition_deal(deal_id, "assigned", actor="operator",
                        reason="Gate B approved", db=db)

        log_agent_event(
            source_agent="dispo-coordinator",
            target_agent="operator",
            payload={"event_type": "GATE_B_APPROVED", "deal_id": deal_id,
                     "approved_by": body.approved_by, "esign_id": esign_id},
            response={}, status_code=200,
        )
        log.info("Gate B approved: deal #%d", deal_id)

    return {"status": "assigned", "deal_id": deal_id, "assignment_esign_id": esign_id}


@app.post("/gate-b/{deal_id}/reject")
def reject_gate_b(deal_id: int, body: RejectGateBRequest):
    """Reject Gate B — deal returns to in_dispo for buyer re-selection."""
    with session_ctx() as db:
        item = fetch_gate_b_item(db, deal_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Deal not in Gate B queue")
        record_gate_b_decision(db, deal_id, approved=False,
                               approved_by=body.approved_by, reason=body.reason)
        transition_deal(deal_id, "in_dispo", actor="operator",
                        reason=f"Gate B rejected: {body.reason}", db=db)
        log_agent_event(
            source_agent="dispo-coordinator",
            target_agent="operator",
            payload={"event_type": "GATE_B_REJECTED", "deal_id": deal_id,
                     "reason": body.reason},
            response={}, status_code=200,
        )
    return {"status": "in_dispo", "deal_id": deal_id}


# ── Closing pipeline endpoints ─────────────────────────────────────────────────

@app.post("/deal/{deal_id}/title-open")
def title_open(deal_id: int, body: TitleOpenRequest):
    with session_ctx() as db:
        db.execute(
            text("UPDATE deals SET title_company = :tc WHERE id = :id"),
            {"tc": body.title_company, "id": deal_id},
        )
        transition_deal(deal_id, "title_open_w", actor=body.actor,
                        reason="Title company engaged", db=db)
        db.commit()
    return {"status": "title_open_w", "deal_id": deal_id}


@app.post("/deal/{deal_id}/clear-to-close")
def clear_to_close(deal_id: int, actor: str = "dispo-coordinator"):
    with session_ctx() as db:
        transition_deal(deal_id, "clear_to_close_w", actor=actor,
                        reason="Title cleared", db=db)
    return {"status": "clear_to_close_w", "deal_id": deal_id}


@app.post("/deal/{deal_id}/closed")
def record_closed(deal_id: int, body: ClosedRequest):
    with session_ctx() as db:
        db.execute(
            text("UPDATE deals SET closing_confirmed_at = NOW() WHERE id = :id"),
            {"id": deal_id},
        )
        transition_deal(deal_id, "closed_w", actor=body.actor,
                        reason="Closing confirmed", db=db)
        db.commit()
    return {"status": "closed_w", "deal_id": deal_id}


@app.post("/deal/{deal_id}/fee-received")
def record_fee_received(deal_id: int, body: FeeReceivedRequest):
    """Record assignment fee receipt — terminal state for wholesale track."""
    with session_ctx() as db:
        db.execute(
            text("""
                UPDATE deals
                SET fee_received_at     = NOW(),
                    fee_received_amount = :amt
                WHERE id = :id
            """),
            {"amt": body.amount_received, "id": deal_id},
        )
        transition_deal(deal_id, "fee_received", actor=body.actor,
                        reason=f"Fee received: ${body.amount_received:,.0f}", db=db)
        # Also flip lead status to reflect completion
        lead_row = db.execute(
            text("SELECT lead_id FROM deals WHERE id = :id"),
            {"id": deal_id},
        ).fetchone()
        if lead_row:
            db.execute(
                text("UPDATE leads SET status = 'fee_received', updated_at = NOW() WHERE id = :id"),
                {"id": lead_row[0]},
            )
        db.commit()
        log_agent_event(
            source_agent="dispo-coordinator",
            target_agent="orchestrator",
            payload={"event_type": "FEE_RECEIVED", "deal_id": deal_id,
                     "amount": body.amount_received},
            response={}, status_code=200,
        )
        log.info("Fee received for deal #%d: $%.0f", deal_id, body.amount_received)
    return {"status": "fee_received", "deal_id": deal_id, "amount": body.amount_received}


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    import uvicorn
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [dispo-coordinator] %(message)s",
    )
    uvicorn.run(
        "agents.dispo_coordinator.run:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8011")),
        reload=False,
    )


if __name__ == "__main__":
    main()
