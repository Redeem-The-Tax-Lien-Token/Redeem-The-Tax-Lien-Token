"""
Agent 13 — Rehab Manager (FastAPI service).

Endpoints:
  POST /rehab/scope/{deal_id}               — submit scope of work (→ Gate C queue)
  GET  /rehab/scope/{deal_id}               — read scope + line items
  POST /rehab/contractor                    — register a contractor
  GET  /rehab/contractors                   — list active contractors
  POST /rehab/bid/{deal_id}                 — record a contractor bid
  GET  /rehab/bids/{deal_id}                — list bids (with ranking)
  POST /rehab/bid/{deal_id}/select/{cid}    — select contractor (→ scope_ready)
  GET  /gate-c/queue                        — Gate C approval queue
  GET  /gate-c/{deal_id}                    — Gate C item detail
  POST /gate-c/{deal_id}/approve            — approve scope + contractor
  POST /gate-c/{deal_id}/reject             — send back for revision
  POST /rehab/draw/{deal_id}                — request a draw (→ Gate B)
  GET  /rehab/draws/{deal_id}               — list draw requests
  POST /rehab/change-order/{deal_id}        — submit a change order
  GET  /rehab/change-orders/{deal_id}       — list change orders
  POST /gate-c/change-order/{co_id}/approve — approve a change order
  POST /gate-c/change-order/{co_id}/reject  — reject a change order
  POST /rehab/{deal_id}/complete            — mark rehab complete
  GET  /                                    — health check
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from shared.db import session_ctx, log_agent_event
from shared.state_machine import transition_deal

from .scope_builder import ScopeItem, total_scope_budget, validate_scope
from .contractor import qualify_contractor, rank_bids
from .draws import validate_draw_request, total_drawn, remaining_budget
from .gate_c_queue import (
    fetch_gate_c_queue, fetch_gate_c_item, fetch_change_orders_pending,
    record_gate_c_decision, record_co_decision, validate_change_order,
)

log = logging.getLogger(__name__)

app = FastAPI(title="Agent 13 — Rehab Manager", version="0.1.0")


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_deal(db, deal_id: int) -> dict:
    row = db.execute(
        text("""
            SELECT d.id, d.strategy, d.total_rehab_budget, d.rehab_spent,
                   d.selected_contractor_id, d.gate_c_approved_at,
                   l.id AS lead_id, l.status AS lead_status,
                   l.address, l.city
            FROM deals d
            JOIN leads l ON l.id = d.lead_id
            WHERE d.id = :did
        """),
        {"did": deal_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Deal not found")
    return dict(row._mapping)


def _require_brrrr(deal: dict) -> None:
    if deal.get("strategy") != "brrrr":
        raise HTTPException(status_code=422, detail="This endpoint is for BRRRR deals only")


# ── Scope of work ─────────────────────────────────────────────────────────────

class ScopeItemInput(BaseModel):
    category:    str
    description: str
    total_cost:  float
    quantity:    float | None = None
    unit:        str | None   = None
    unit_cost:   float | None = None


class ScopeSubmit(BaseModel):
    items:  list[ScopeItemInput]
    notes:  str = ""


@app.post("/rehab/scope/{deal_id}")
def submit_scope(deal_id: int, body: ScopeSubmit):
    """Submit a scope of work — creates a pending_gate_c SOW record."""
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        _require_brrrr(deal)

        items = [
            ScopeItem(
                category=i.category, description=i.description,
                total_cost=i.total_cost, quantity=i.quantity,
                unit=i.unit, unit_cost=i.unit_cost, sort_order=idx,
            )
            for idx, i in enumerate(body.items)
        ]

        errors = validate_scope(items)
        if errors:
            raise HTTPException(status_code=422, detail={"errors": errors})

        budget = total_scope_budget(items)

        # Get next version number
        version = (db.execute(
            text("SELECT COALESCE(MAX(version), 0) + 1 FROM scope_of_work WHERE deal_id = :did"),
            {"did": deal_id},
        ).scalar()) or 1

        # Supersede any previous draft
        db.execute(
            text("""
                UPDATE scope_of_work SET status = 'superseded'
                WHERE deal_id = :did AND status IN ('draft', 'pending_gate_c')
            """),
            {"did": deal_id},
        )

        sow_id = db.execute(
            text("""
                INSERT INTO scope_of_work
                    (deal_id, version, status, total_budget, notes)
                VALUES (:did, :ver, 'pending_gate_c', :budget, :notes)
                RETURNING id
            """),
            {"did": deal_id, "ver": version, "budget": budget, "notes": body.notes},
        ).scalar()

        for item in items:
            db.execute(
                text("""
                    INSERT INTO scope_items
                        (sow_id, category, description, quantity, unit, unit_cost,
                         total_cost, sort_order)
                    VALUES (:sid, :cat, :desc, :qty, :unit, :uc, :tc, :so)
                """),
                {
                    "sid": sow_id, "cat": item.category, "desc": item.description,
                    "qty": item.quantity, "unit": item.unit, "uc": item.unit_cost,
                    "tc": item.total_cost, "so": item.sort_order,
                },
            )

        db.execute(
            text("UPDATE deals SET total_rehab_budget = :budget WHERE id = :did"),
            {"budget": budget, "did": deal_id},
        )
        db.commit()

        log_agent_event(
            source_agent="rehab",
            target_agent="operator",
            payload={"event_type": "SCOPE_SUBMITTED", "deal_id": deal_id,
                     "sow_id": sow_id, "total_budget": budget, "item_count": len(items)},
            response={}, status_code=200,
        )

    return {"status": "pending_gate_c", "sow_id": sow_id, "total_budget": budget}


@app.get("/rehab/scope/{deal_id}")
def get_scope(deal_id: int):
    with session_ctx() as db:
        _get_deal(db, deal_id)
        sow = db.execute(
            text("""
                SELECT id, version, status, total_budget, notes, approved_at
                FROM scope_of_work
                WHERE deal_id = :did
                ORDER BY version DESC LIMIT 1
            """),
            {"did": deal_id},
        ).fetchone()
        if not sow:
            return {"deal_id": deal_id, "scope": None}

        items = db.execute(
            text("""
                SELECT category, description, quantity, unit, unit_cost, total_cost, sort_order
                FROM scope_items WHERE sow_id = :sid ORDER BY sort_order
            """),
            {"sid": sow.id},
        ).fetchall()

    return {
        "deal_id": deal_id,
        "scope": dict(sow._mapping),
        "items": [dict(i._mapping) for i in items],
    }


# ── Contractors ───────────────────────────────────────────────────────────────

class ContractorCreate(BaseModel):
    name:               str
    phone:              str | None = None
    email:              str | None = None
    license_number:     str | None = None
    license_verified:   bool = False
    insurance_verified: bool = False
    insurance_expiry:   str | None = None   # ISO date string
    specialty:          list[str] = []
    tier_rating:        str | None = None
    notes:              str | None = None


@app.post("/rehab/contractor")
def create_contractor(body: ContractorCreate):
    qual = qualify_contractor(body.model_dump())
    with session_ctx() as db:
        cid = db.execute(
            text("""
                INSERT INTO contractors
                    (name, phone, email, license_number, license_verified,
                     insurance_verified, insurance_expiry, specialty, tier_rating, notes)
                VALUES
                    (:name, :phone, :email, :lic, :lv, :iv, :ie, :spec, :rating, :notes)
                RETURNING id
            """),
            {
                "name":   body.name, "phone": body.phone, "email": body.email,
                "lic":    body.license_number, "lv": body.license_verified,
                "iv":     body.insurance_verified, "ie": body.insurance_expiry,
                "spec":   body.specialty, "rating": body.tier_rating,
                "notes":  body.notes,
            },
        ).scalar()
        db.commit()

    return {
        "contractor_id": cid,
        "qualified":     qual.qualified,
        "reasons":       qual.reasons,
    }


@app.get("/rehab/contractors")
def list_contractors():
    with session_ctx() as db:
        rows = db.execute(
            text("""
                SELECT id, name, phone, license_verified, insurance_verified,
                       insurance_expiry, tier_rating, active
                FROM contractors WHERE active = TRUE ORDER BY name
            """)
        ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Bids ──────────────────────────────────────────────────────────────────────

class BidCreate(BaseModel):
    contractor_id: int
    bid_amount:    float
    timeline_days: int | None = None
    notes:         str | None = None


@app.post("/rehab/bid/{deal_id}")
def record_bid(deal_id: int, body: BidCreate):
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        _require_brrrr(deal)

        # Check contractor exists and qualify them
        ctr = db.execute(
            text("SELECT * FROM contractors WHERE id = :cid"),
            {"cid": body.contractor_id},
        ).fetchone()
        if not ctr:
            raise HTTPException(status_code=404, detail="Contractor not found")

        ctr_dict = dict(ctr._mapping)
        qual     = qualify_contractor(ctr_dict)

        sow = db.execute(
            text("SELECT id FROM scope_of_work WHERE deal_id = :did ORDER BY version DESC LIMIT 1"),
            {"did": deal_id},
        ).fetchone()

        bid_id = db.execute(
            text("""
                INSERT INTO contractor_bids
                    (deal_id, sow_id, contractor_id, bid_amount, timeline_days, notes,
                     status)
                VALUES (:did, :sow, :cid, :amt, :days, :notes, :status)
                ON CONFLICT (deal_id, contractor_id) DO UPDATE
                    SET bid_amount = EXCLUDED.bid_amount,
                        timeline_days = EXCLUDED.timeline_days,
                        notes = EXCLUDED.notes,
                        updated_at = NOW()
                RETURNING id
            """),
            {
                "did": deal_id, "sow": sow.id if sow else None,
                "cid": body.contractor_id, "amt": body.bid_amount,
                "days": body.timeline_days, "notes": body.notes,
                "status": "qualified" if qual.qualified else "disqualified",
            },
        ).scalar()
        db.commit()

    return {
        "bid_id":    bid_id,
        "qualified": qual.qualified,
        "reasons":   qual.reasons,
    }


@app.get("/rehab/bids/{deal_id}")
def list_bids(deal_id: int):
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        rows = db.execute(
            text("""
                SELECT cb.id, cb.contractor_id, c.name AS contractor_name,
                       c.tier_rating, cb.bid_amount, cb.timeline_days,
                       cb.status, cb.notes
                FROM contractor_bids cb
                JOIN contractors c ON c.id = cb.contractor_id
                WHERE cb.deal_id = :did
                ORDER BY cb.bid_amount
            """),
            {"did": deal_id},
        ).fetchall()

    raw      = [dict(r._mapping) for r in rows]
    qual_bids = [b for b in raw if b["status"] == "qualified"]
    budget    = float(deal.get("total_rehab_budget") or 0)
    ranked    = rank_bids(qual_bids, budget)

    return {"deal_id": deal_id, "bids": raw, "ranked_qualified": ranked}


@app.post("/rehab/bid/{deal_id}/select/{contractor_id}")
def select_contractor(deal_id: int, contractor_id: int):
    """Select a contractor and advance to scope_ready for Gate C."""
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        _require_brrrr(deal)

        bid = db.execute(
            text("""
                SELECT id, status FROM contractor_bids
                WHERE deal_id = :did AND contractor_id = :cid
            """),
            {"did": deal_id, "cid": contractor_id},
        ).fetchone()
        if not bid:
            raise HTTPException(status_code=404, detail="No bid from this contractor for this deal")
        if bid.status == "disqualified":
            raise HTTPException(status_code=422,
                                detail="Cannot select a disqualified contractor")

        db.execute(
            text("""
                UPDATE contractor_bids SET status = 'selected', updated_at = NOW()
                WHERE deal_id = :did AND contractor_id = :cid
            """),
            {"did": deal_id, "cid": contractor_id},
        )
        db.execute(
            text("UPDATE deals SET selected_contractor_id = :cid WHERE id = :did"),
            {"cid": contractor_id, "did": deal_id},
        )
        transition_deal(db, deal_id, "scope_ready", actor="agent_13",
                        reason=f"Contractor {contractor_id} selected — awaiting Gate C")
        db.commit()

        log_agent_event(
            source_agent="rehab",
            target_agent="operator",
            payload={"event_type": "CONTRACTOR_SELECTED", "deal_id": deal_id,
                     "contractor_id": contractor_id},
            response={}, status_code=200,
        )

    return {"status": "scope_ready", "deal_id": deal_id, "selected_contractor_id": contractor_id}


# ── Gate C ────────────────────────────────────────────────────────────────────

@app.get("/gate-c/queue")
def gate_c_queue():
    with session_ctx() as db:
        return {"queue": fetch_gate_c_queue(db)}


@app.get("/gate-c/{deal_id}")
def gate_c_item(deal_id: int):
    with session_ctx() as db:
        item = fetch_gate_c_item(db, deal_id)
        if not item:
            raise HTTPException(status_code=404, detail="Deal not found or not in Gate C queue")
        pending_cos = fetch_change_orders_pending(db, deal_id)
    return {"item": item, "pending_change_orders": pending_cos}


class GateCDecision(BaseModel):
    approved:    bool
    approved_by: str = Field("operator")
    reason:      str = ""


@app.post("/gate-c/{deal_id}/approve")
def gate_c_approve(deal_id: int, body: GateCDecision):
    body.approved = True
    with session_ctx() as db:
        item = fetch_gate_c_item(db, deal_id)
        if not item:
            raise HTTPException(status_code=404, detail="Deal not found")
        record_gate_c_decision(db, deal_id, approved=True,
                               approved_by=body.approved_by, reason=body.reason)
        db.execute(
            text("UPDATE deals SET rehab_started_at = NOW() WHERE id = :did"),
            {"did": deal_id},
        )
        db.commit()
        log_agent_event(
            source_agent="rehab",
            target_agent="operator",
            payload={"event_type": "GATE_C_APPROVED", "deal_id": deal_id,
                     "approved_by": body.approved_by},
            response={}, status_code=200,
        )
    return {"status": "rehab_active", "deal_id": deal_id}


@app.post("/gate-c/{deal_id}/reject")
def gate_c_reject(deal_id: int, body: GateCDecision):
    body.approved = False
    if not body.reason:
        raise HTTPException(status_code=422, detail="reason is required for rejection")
    with session_ctx() as db:
        item = fetch_gate_c_item(db, deal_id)
        if not item:
            raise HTTPException(status_code=404, detail="Deal not found")
        record_gate_c_decision(db, deal_id, approved=False,
                               approved_by=body.approved_by, reason=body.reason)
        db.commit()
    return {"status": "scope_ready", "deal_id": deal_id, "reason": body.reason}


# ── Draws ─────────────────────────────────────────────────────────────────────

class DrawRequest(BaseModel):
    amount_requested: float
    description:      str
    milestone_notes:  str = ""
    contractor_id:    int | None = None


@app.post("/rehab/draw/{deal_id}")
def request_draw(deal_id: int, body: DrawRequest):
    """Submit a draw request — creates a pending_gate_b record."""
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        _require_brrrr(deal)

        if deal.get("lead_status") != "rehab_active":
            raise HTTPException(status_code=422,
                                detail=f"Draws only allowed during rehab_active (status={deal.get('lead_status')})")

        existing_draws = db.execute(
            text("SELECT draw_number, amount_requested FROM draw_requests WHERE deal_id = :did"),
            {"did": deal_id},
        ).fetchall()
        draws_so_far = [dict(r._mapping) for r in existing_draws]
        draw_number  = len(draws_so_far) + 1

        draw = {
            "draw_number":       draw_number,
            "amount_requested":  body.amount_requested,
            "description":       body.description,
        }
        budget = float(deal.get("total_rehab_budget") or 0)
        val    = validate_draw_request(draw, budget, draws_so_far)
        if not val.valid:
            raise HTTPException(status_code=422, detail={"errors": val.errors})

        draw_id = db.execute(
            text("""
                INSERT INTO draw_requests
                    (deal_id, draw_number, contractor_id, amount_requested,
                     description, milestone_notes, status)
                VALUES (:did, :num, :cid, :amt, :desc, :ms, 'pending_gate_b')
                RETURNING id
            """),
            {
                "did": deal_id, "num": draw_number, "cid": body.contractor_id,
                "amt": body.amount_requested, "desc": body.description,
                "ms": body.milestone_notes,
            },
        ).scalar()
        db.commit()

        log_agent_event(
            source_agent="rehab",
            target_agent="operator",
            payload={"event_type": "DRAW_REQUEST_SUBMITTED", "deal_id": deal_id,
                     "draw_id": draw_id, "draw_number": draw_number,
                     "amount": body.amount_requested},
            response={}, status_code=200,
        )

    return {"status": "pending_gate_b", "draw_id": draw_id, "draw_number": draw_number}


@app.get("/rehab/draws/{deal_id}")
def list_draws(deal_id: int):
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        rows = db.execute(
            text("""
                SELECT id, draw_number, amount_requested, amount_approved,
                       description, status, released_at, created_at
                FROM draw_requests WHERE deal_id = :did ORDER BY draw_number
            """),
            {"did": deal_id},
        ).fetchall()

    draws = [dict(r._mapping) for r in rows]
    budget = float(deal.get("total_rehab_budget") or 0)
    return {
        "deal_id":    deal_id,
        "draws":      draws,
        "total_drawn": total_drawn(draws),
        "remaining":   remaining_budget(budget, draws),
        "budget":      budget,
    }


# ── Change orders ─────────────────────────────────────────────────────────────

class ChangeOrderCreate(BaseModel):
    reason:       str
    amount_delta: float


@app.post("/rehab/change-order/{deal_id}")
def submit_change_order(deal_id: int, body: ChangeOrderCreate):
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        _require_brrrr(deal)

        existing_cos = db.execute(
            text("""
                SELECT co_number, amount_delta FROM change_orders
                WHERE deal_id = :did AND status = 'approved'
            """),
            {"did": deal_id},
        ).fetchall()
        approved_cos = [dict(r._mapping) for r in existing_cos]

        co_number = db.execute(
            text("SELECT COALESCE(MAX(co_number), 0) + 1 FROM change_orders WHERE deal_id = :did"),
            {"did": deal_id},
        ).scalar()

        budget = float(deal.get("total_rehab_budget") or 0)
        co = {"co_number": co_number, "reason": body.reason, "amount_delta": body.amount_delta}
        val = validate_change_order(co, budget, approved_cos)

        if not val.valid:
            raise HTTPException(status_code=422, detail={"errors": val.errors})

        previous_co_total = sum(float(c.get("amount_delta") or 0) for c in approved_cos)
        new_total = budget + previous_co_total + body.amount_delta

        co_id = db.execute(
            text("""
                INSERT INTO change_orders
                    (deal_id, co_number, reason, amount_delta, new_total,
                     requires_gate_c, status)
                VALUES (:did, :num, :reason, :delta, :new_total, :rgc,
                        CASE WHEN :rgc THEN 'pending' ELSE 'approved' END)
                RETURNING id
            """),
            {
                "did": deal_id, "num": co_number, "reason": body.reason,
                "delta": body.amount_delta, "new_total": new_total,
                "rgc": val.requires_gate_c,
            },
        ).scalar()

        if not val.requires_gate_c:
            # Auto-approve cost-neutral or cost-saving COs
            db.execute(
                text("UPDATE deals SET total_rehab_budget = :nt WHERE id = :did"),
                {"nt": new_total, "did": deal_id},
            )

        db.commit()

    return {
        "co_id":           co_id,
        "co_number":       co_number,
        "requires_gate_c": val.requires_gate_c,
        "new_total":       new_total,
        "status":          "pending" if val.requires_gate_c else "approved",
    }


@app.get("/rehab/change-orders/{deal_id}")
def list_change_orders(deal_id: int):
    with session_ctx() as db:
        _get_deal(db, deal_id)
        rows = db.execute(
            text("""
                SELECT id, co_number, reason, amount_delta, new_total,
                       requires_gate_c, status, created_at
                FROM change_orders WHERE deal_id = :did ORDER BY co_number
            """),
            {"did": deal_id},
        ).fetchall()
    return [dict(r._mapping) for r in rows]


class CODecision(BaseModel):
    approved:    bool
    approved_by: str = "operator"
    reason:      str = ""


@app.post("/gate-c/change-order/{co_id}/approve")
def approve_co(co_id: int, body: CODecision):
    body.approved = True
    with session_ctx() as db:
        record_co_decision(db, co_id, approved=True, approved_by=body.approved_by)
        db.commit()
    return {"status": "approved", "co_id": co_id}


@app.post("/gate-c/change-order/{co_id}/reject")
def reject_co(co_id: int, body: CODecision):
    if not body.reason:
        raise HTTPException(status_code=422, detail="reason required for rejection")
    with session_ctx() as db:
        record_co_decision(db, co_id, approved=False, approved_by=body.approved_by,
                           reason=body.reason)
        db.commit()
    return {"status": "rejected", "co_id": co_id}


# ── Rehab complete ────────────────────────────────────────────────────────────

@app.post("/rehab/{deal_id}/complete")
def mark_rehab_complete(deal_id: int):
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        if deal.get("lead_status") != "rehab_active":
            raise HTTPException(
                status_code=422,
                detail=f"Deal is not in rehab_active (status={deal.get('lead_status')})",
            )
        db.execute(
            text("UPDATE deals SET rehab_complete_at = NOW() WHERE id = :did"),
            {"did": deal_id},
        )
        transition_deal(db, deal_id, "rehab_complete", actor="agent_13",
                        reason="Rehab marked complete")
        db.commit()
        log_agent_event(
            source_agent="rehab",
            target_agent="operator",
            payload={"event_type": "REHAB_COMPLETE", "deal_id": deal_id},
            response={}, status_code=200,
        )
    return {"status": "rehab_complete", "deal_id": deal_id}


@app.get("/")
def health():
    return {"status": "ok", "agent": "rehab"}


def main() -> None:
    import uvicorn
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [rehab] %(message)s",
    )
    uvicorn.run(
        "agents.rehab.run:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8013")),
        reload=False,
    )


if __name__ == "__main__":
    main()
