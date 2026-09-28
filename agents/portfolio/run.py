"""
Agent 16 — Portfolio Manager (FastAPI service).

Manages owned BRRRR rentals only — never third-party management.

Endpoints:
  POST /portfolio/rent/{deal_id}           — record a rent payment
  GET  /portfolio/rent/{deal_id}           — list rent payments (+ status)
  POST /portfolio/maintenance/{deal_id}    — submit a maintenance request
  PATCH /portfolio/maintenance/{req_id}   — update maintenance status/cost
  GET  /portfolio/performance/{deal_id}   — monthly performance vs. pro forma
  GET  /portfolio/performance             — all stabilized deals performance
  POST /portfolio/late-notice/{deal_id}   — generate late-payment notice
  POST /portfolio/stabilize/{deal_id}     — mark deal as STABILIZED (portfolio)
  GET  /                                  — health check

Contract (§7, Agent 16):
  - Owner-managed properties only. Never manages third-party properties.
  - Late notices are from templates per landlord_tenant.yaml deadlines.
  - Underperformer flags feed back into §3 strategy defaults over time.
  - State-specific deadlines (Indiana IC 32-31) are in landlord_tenant.yaml.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import yaml as _yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from shared.db import session_ctx, log_agent_event
from shared.state_machine import transition_deal

from .performance import (
    compute_performance, flag_underperformer, late_notice_due, PerformanceMetrics,
)

log = logging.getLogger(__name__)

app = FastAPI(title="Agent 16 — Portfolio Manager", version="0.1.0")

_LT_PATH       = Path(__file__).parent.parent.parent / "config" / "landlord_tenant.yaml"
_STRATEGY_PATH = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"


def _load_lt_cfg() -> dict:
    return _yaml.safe_load(_LT_PATH.read_text())


def _load_cfg() -> dict:
    return _yaml.safe_load(_STRATEGY_PATH.read_text())


def _get_deal(db, deal_id: int) -> dict:
    row = db.execute(
        text("""
            SELECT d.id, d.strategy, d.rent_actual, d.cash_flow_actual,
                   d.dscr_actual, d.underwriting_snapshot,
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


# ── Rent payments ─────────────────────────────────────────────────────────────

class RentPaymentCreate(BaseModel):
    lease_id:    int
    due_date:    str          # ISO date
    amount_due:  float
    amount_paid: float | None = None
    paid_at:     str | None   = None   # ISO datetime
    notes:       str          = ""


@app.post("/portfolio/rent/{deal_id}")
def record_rent_payment(deal_id: int, body: RentPaymentCreate):
    with session_ctx() as db:
        _get_deal(db, deal_id)

        amount_paid = body.amount_paid
        status = "pending"
        if amount_paid is not None:
            if amount_paid >= body.amount_due:
                status = "paid"
            elif amount_paid > 0:
                status = "partial"

        # Check if late (due_date < today and not paid)
        due = date.fromisoformat(body.due_date)
        today = datetime.now(tz=timezone.utc).date()
        if status == "pending" and due < today:
            status = "late"

        payment_id = db.execute(
            text("""
                INSERT INTO rent_payments
                    (lease_id, deal_id, due_date, amount_due, amount_paid,
                     paid_at, status, notes)
                VALUES (:lid, :did, :due, :adu, :apd, :pat, :status, :notes)
                ON CONFLICT (lease_id, due_date) DO UPDATE
                    SET amount_paid = EXCLUDED.amount_paid,
                        paid_at = EXCLUDED.paid_at,
                        status  = EXCLUDED.status,
                        notes   = EXCLUDED.notes
                RETURNING id
            """),
            {
                "lid":    body.lease_id,
                "did":    deal_id,
                "due":    body.due_date,
                "adu":    body.amount_due,
                "apd":    amount_paid,
                "pat":    body.paid_at,
                "status": status,
                "notes":  body.notes,
            },
        ).scalar()
        db.commit()

    return {"payment_id": payment_id, "status": status}


@app.get("/portfolio/rent/{deal_id}")
def list_rent_payments(deal_id: int):
    with session_ctx() as db:
        _get_deal(db, deal_id)
        rows = db.execute(
            text("""
                SELECT id, lease_id, due_date, amount_due, amount_paid,
                       paid_at, status, late_fee, notes
                FROM rent_payments
                WHERE deal_id = :did
                ORDER BY due_date DESC
            """),
            {"did": deal_id},
        ).fetchall()

    payments = [dict(r._mapping) for r in rows]
    overdue  = [p for p in payments if p["status"] in ("late", "unpaid")]
    return {"deal_id": deal_id, "payments": payments, "overdue_count": len(overdue)}


# ── Maintenance ───────────────────────────────────────────────────────────────

class MaintenanceCreate(BaseModel):
    description: str
    priority:    str = "normal"   # 'emergency' | 'urgent' | 'normal'
    category:    str | None = None
    lease_id:    int | None = None


@app.post("/portfolio/maintenance/{deal_id}")
def submit_maintenance(deal_id: int, body: MaintenanceCreate):
    if body.priority not in ("emergency", "urgent", "normal"):
        raise HTTPException(status_code=400, detail="priority must be emergency | urgent | normal")

    with session_ctx() as db:
        _get_deal(db, deal_id)

        req_id = db.execute(
            text("""
                INSERT INTO maintenance_requests
                    (deal_id, lease_id, category, description, priority)
                VALUES (:did, :lid, :cat, :desc, :prio)
                RETURNING id
            """),
            {
                "did":  deal_id, "lid": body.lease_id,
                "cat":  body.category, "desc": body.description,
                "prio": body.priority,
            },
        ).scalar()
        db.commit()

        if body.priority == "emergency":
            log_agent_event(
                source_agent="portfolio",
                target_agent="operator",
                payload={"event_type": "EMERGENCY_MAINTENANCE", "deal_id": deal_id,
                         "request_id": req_id, "description": body.description},
                response={}, status_code=200,
            )

    return {"request_id": req_id, "status": "open"}


class MaintenanceUpdate(BaseModel):
    status:        str | None = None
    cost:          float | None = None
    contractor_id: int | None  = None
    notes:         str | None  = None


@app.patch("/portfolio/maintenance/{req_id}")
def update_maintenance(req_id: int, body: MaintenanceUpdate):
    with session_ctx() as db:
        updates = []
        params  = {"rid": req_id}

        if body.status:
            if body.status not in ("open", "in_progress", "resolved", "deferred"):
                raise HTTPException(status_code=400, detail="Invalid status")
            updates.append("status = :status")
            params["status"] = body.status
            if body.status == "resolved":
                updates.append("resolved_at = NOW()")
        if body.cost is not None:
            updates.append("cost = :cost")
            params["cost"] = body.cost
        if body.contractor_id is not None:
            updates.append("contractor_id = :cid")
            params["cid"] = body.contractor_id
        if body.notes is not None:
            updates.append("notes = :notes")
            params["notes"] = body.notes

        if not updates:
            raise HTTPException(status_code=422, detail="No fields to update")

        updates.append("updated_at = NOW()")
        db.execute(
            text(f"UPDATE maintenance_requests SET {', '.join(updates)} WHERE id = :rid"),
            params,
        )
        db.commit()

    return {"request_id": req_id, "updated": True}


# ── Performance ───────────────────────────────────────────────────────────────

@app.get("/portfolio/performance/{deal_id}")
def get_performance(deal_id: int):
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)

        payments = db.execute(
            text("""
                SELECT amount_due, amount_paid, status, due_date
                FROM rent_payments WHERE deal_id = :did
                AND due_date >= date_trunc('year', NOW())
            """),
            {"did": deal_id},
        ).fetchall()

        maint = db.execute(
            text("""
                SELECT cost FROM maintenance_requests
                WHERE deal_id = :did
                AND created_at >= date_trunc('year', NOW())
                AND status != 'deferred'
            """),
            {"did": deal_id},
        ).fetchall()

    cfg      = _load_cfg()
    payments_list = [dict(r._mapping) for r in payments]
    maint_list    = [dict(r._mapping) for r in maint]

    metrics = compute_performance(deal, payments_list, maint_list, cfg)
    flags   = flag_underperformer(metrics, cfg)
    metrics.performance_flags = flags

    return {
        "deal_id":            metrics.deal_id,
        "rent_actual":        metrics.rent_actual,
        "rent_pro_forma":     metrics.rent_pro_forma,
        "rent_variance":      metrics.rent_variance,
        "cash_flow_actual":   metrics.cash_flow_actual,
        "cash_flow_pro_forma": metrics.cash_flow_pro_forma,
        "cash_flow_variance": metrics.cash_flow_variance,
        "dscr_actual":        metrics.dscr_actual,
        "vacancy_months":     metrics.vacancy_months,
        "maintenance_ytd":    metrics.maintenance_ytd,
        "performance_flags":  flags,
        "recommendation": "HOLD" if not flags else "REVIEW",
    }


@app.get("/portfolio/performance")
def get_all_performance():
    """Performance summary for all stabilized/leased deals."""
    with session_ctx() as db:
        rows = db.execute(
            text("""
                SELECT d.id, l.address, l.city, d.rent_actual,
                       d.cash_flow_actual, d.dscr_actual
                FROM deals d
                JOIN leads l ON l.id = d.lead_id
                WHERE l.status IN ('stabilized', 'leased', 'seasoning',
                                   'refi_applied', 'appraised', 'refinanced')
                AND d.strategy = 'brrrr'
                ORDER BY l.address
            """)
        ).fetchall()

    return {"portfolio": [dict(r._mapping) for r in rows]}


# ── Late notices ──────────────────────────────────────────────────────────────

@app.post("/portfolio/late-notice/{deal_id}")
def send_late_notice(deal_id: int):
    """
    Generate and log a late-payment notice for all overdue payments.

    Uses Indiana IC 32-31-1-6 10-day notice from landlord_tenant.yaml.
    Actual sending (mail, email) is performed by the operator or a connected
    delivery adapter — this endpoint logs the notice and records it.
    """
    with session_ctx() as db:
        _get_deal(db, deal_id)
        lt_cfg = _load_lt_cfg()
        notice_days = lt_cfg.get("notices", {}).get("pay_or_quit_days", 10)

        overdue = db.execute(
            text("""
                SELECT rp.id, rp.lease_id, rp.due_date, rp.amount_due,
                       rp.amount_paid, rp.status,
                       l.tenant_name, l.tenant_email
                FROM rent_payments rp
                JOIN leases l ON l.id = rp.lease_id
                WHERE rp.deal_id = :did
                AND rp.status IN ('late', 'unpaid', 'partial')
                ORDER BY rp.due_date
            """),
            {"did": deal_id},
        ).fetchall()

    notices = []
    today   = datetime.now(tz=timezone.utc).date()
    for row in overdue:
        r = dict(row._mapping)
        if late_notice_due(r, today):
            notices.append({
                "payment_id":    r["id"],
                "tenant_name":   r["tenant_name"],
                "tenant_email":  r["tenant_email"],
                "due_date":      str(r["due_date"]),
                "amount_due":    float(r["amount_due"]),
                "amount_paid":   float(r.get("amount_paid") or 0),
                "notice_type":   "pay_or_quit",
                "notice_days":   notice_days,
                "notice_text":   (
                    f"NOTICE TO PAY OR VACATE\n\n"
                    f"Dear {r['tenant_name']},\n\n"
                    f"You have {notice_days} days to pay the outstanding rent of "
                    f"${float(r['amount_due']) - float(r.get('amount_paid') or 0):,.2f} "
                    f"due {r['due_date']}, or vacate the premises.\n\n"
                    f"This notice is required under Indiana Code IC 32-31-1-6."
                ),
            })

    return {"deal_id": deal_id, "notices_generated": len(notices), "notices": notices}


# ── Stabilize ─────────────────────────────────────────────────────────────────

@app.post("/portfolio/stabilize/{deal_id}")
def stabilize(deal_id: int):
    """Mark a refinanced deal as STABILIZED — it joins the permanent portfolio."""
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        if deal.get("lead_status") != "refinanced":
            raise HTTPException(
                status_code=422,
                detail=f"Only refinanced deals can be stabilized (status={deal.get('lead_status')})",
            )
        transition_deal(db, deal_id, "stabilized", actor="agent_16",
                        reason="Deal refinanced and stabilized in portfolio")
        db.commit()
        log_agent_event(
            source_agent="portfolio",
            target_agent="operator",
            payload={"event_type": "STABILIZED", "deal_id": deal_id},
            response={}, status_code=200,
        )
    return {"status": "stabilized", "deal_id": deal_id}


@app.get("/")
def health():
    return {"status": "ok", "agent": "portfolio"}


def main() -> None:
    import uvicorn
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [portfolio] %(message)s",
    )
    uvicorn.run(
        "agents.portfolio.run:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8016")),
        reload=False,
    )


if __name__ == "__main__":
    main()
