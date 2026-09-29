"""
Agent 12 — Acquisition & Funding (FastAPI service).

Handles the BRRRR track from UNDER_CONTRACT(B) through FUNDING_SECURED.

Endpoints:
  POST /acquisition/dd/{deal_id}/init          — seed due-diligence checklist
  POST /acquisition/dd/{deal_id}/check/{name}  — update a single DD check
  GET  /acquisition/dd/{deal_id}               — read checklist + status
  POST /acquisition/dd/{deal_id}/advance       — advance to FUNDING_SECURED if DD complete
  GET  /acquisition/lenders                    — list acquisition lender profiles
  POST /acquisition/lenders/recommend/{deal_id}— recommend lender and build packet
  POST /acquisition/strategy-switch/{deal_id} — propose STRATEGY_SWITCH (BRRRR → wholesale)
  GET  /                                       — health check

Contract (§7, Agent 12):
  - Only BRRRR-track deals advance through this agent.
  - DD is fail-closed: a single pending or failed check blocks FUNDING_SECURED.
  - Lender profiles older than 45 days block eligibility — fail closed per §2.3.
  - strategy-switch is only allowed inside the inspection/due-diligence window.
  - All state transitions go through shared.state_machine.
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

from .due_diligence import (
    DD_CHECKS, initial_dd_rows, evaluate_dd_status, can_advance_to_funding,
)
from .lender_packet import (
    load_acquisition_lenders, validate_lender_profiles, build_lender_packet,
    recommend_lender,
)

log = logging.getLogger(__name__)

app = FastAPI(title="Agent 12 — Acquisition & Funding", version="0.1.0")

_BRRRR_STATUSES_FOR_DD = frozenset({
    "under_contract", "due_diligence", "funding_secured",
})


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_deal(db, deal_id: int) -> dict:
    row = db.execute(
        text("""
            SELECT d.id, d.strategy, d.closing_date, d.inspection_period_days,
                   d.dd_complete_at, d.funding_secured_at,
                   l.id AS lead_id, l.status AS lead_status,
                   l.address, l.city, l.owner_name,
                   d.underwriting_snapshot
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
        raise HTTPException(
            status_code=422,
            detail=f"Deal {deal['id']} is not a BRRRR deal (strategy={deal.get('strategy')})",
        )


# ── DD init ───────────────────────────────────────────────────────────────────

@app.post("/acquisition/dd/{deal_id}/init")
def init_dd(deal_id: int):
    """Seed the due-diligence checklist for a BRRRR deal."""
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        _require_brrrr(deal)

        existing = db.execute(
            text("SELECT count(*) FROM due_diligence WHERE deal_id = :did"),
            {"did": deal_id},
        ).scalar()
        if existing:
            return {"status": "already_initialized", "check_count": existing}

        rows = initial_dd_rows(deal_id)
        for r in rows:
            db.execute(
                text("""
                    INSERT INTO due_diligence (deal_id, check_name, status)
                    VALUES (:deal_id, :check_name, :status)
                    ON CONFLICT (deal_id, check_name) DO NOTHING
                """),
                r,
            )

        transition_deal(db, deal_id, "due_diligence", actor="agent_12",
                        reason="DD checklist initialized")
        db.commit()

        log_agent_event(
            source_agent="acquisition",
            target_agent="operator",
            payload={"event_type": "DD_INITIALIZED", "deal_id": deal_id,
                     "check_count": len(rows)},
            response={}, status_code=200,
        )

    return {"status": "initialized", "checks": [r["check_name"] for r in rows]}


# ── DD update ─────────────────────────────────────────────────────────────────

class DDUpdate(BaseModel):
    status:     str = Field(..., description="'pass' | 'fail' | 'waived'")
    notes:      str = Field("", description="Free-form notes")
    checked_by: str = Field("agent_12")


@app.post("/acquisition/dd/{deal_id}/check/{check_name}")
def update_dd_check(deal_id: int, check_name: str, body: DDUpdate):
    if check_name not in DD_CHECKS:
        raise HTTPException(status_code=400, detail=f"Unknown check '{check_name}'")
    if body.status not in ("pass", "fail", "waived"):
        raise HTTPException(status_code=400, detail="status must be pass | fail | waived")

    with session_ctx() as db:
        _get_deal(db, deal_id)

        updated = db.execute(
            text("""
                UPDATE due_diligence
                SET status = :status, notes = :notes, checked_by = :by,
                    checked_at = NOW()
                WHERE deal_id = :did AND check_name = :name
            """),
            {"status": body.status, "notes": body.notes, "by": body.checked_by,
             "did": deal_id, "name": check_name},
        ).rowcount

        if not updated:
            raise HTTPException(status_code=404,
                                detail="Check not found — run /init first")
        db.commit()

    return {"status": "updated", "check_name": check_name, "new_status": body.status}


# ── DD read ───────────────────────────────────────────────────────────────────

@app.get("/acquisition/dd/{deal_id}")
def get_dd(deal_id: int):
    with session_ctx() as db:
        _get_deal(db, deal_id)
        rows = db.execute(
            text("""
                SELECT check_name, status, notes, checked_at, checked_by
                FROM due_diligence
                WHERE deal_id = :did
                ORDER BY id
            """),
            {"did": deal_id},
        ).fetchall()

    row_dicts = [dict(r._mapping) for r in rows]
    dd_status = evaluate_dd_status(row_dicts)
    can_advance = can_advance_to_funding(row_dicts)

    return {
        "deal_id":     deal_id,
        "checks":      row_dicts,
        "outcome":     dd_status.outcome,
        "failed":      dd_status.failed_checks,
        "pending":     dd_status.pending_checks,
        "can_advance": can_advance,
    }


# ── DD advance to FUNDING_SECURED ─────────────────────────────────────────────

@app.post("/acquisition/dd/{deal_id}/advance")
def advance_to_funding(deal_id: int):
    """Advance deal to FUNDING_SECURED once all DD checks pass or are waived."""
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        _require_brrrr(deal)

        rows = db.execute(
            text("SELECT check_name, status FROM due_diligence WHERE deal_id = :did"),
            {"did": deal_id},
        ).fetchall()
        row_dicts = [dict(r._mapping) for r in rows]

        if not can_advance_to_funding(row_dicts):
            dd_status = evaluate_dd_status(row_dicts)
            raise HTTPException(
                status_code=422,
                detail=f"DD not complete. outcome={dd_status.outcome} "
                       f"failed={dd_status.failed_checks} "
                       f"pending={dd_status.pending_checks}",
            )

        db.execute(
            text("UPDATE deals SET dd_complete_at = NOW() WHERE id = :did"),
            {"did": deal_id},
        )
        transition_deal(db, deal_id, "funding_secured", actor="agent_12",
                        reason="All DD checks passed")
        db.commit()

        log_agent_event(
            source_agent="acquisition",
            target_agent="operator",
            payload={"event_type": "DD_COMPLETE", "deal_id": deal_id},
            response={}, status_code=200,
        )

    return {"status": "funding_secured", "deal_id": deal_id}


# ── Lenders ───────────────────────────────────────────────────────────────────

@app.get("/acquisition/lenders")
def list_lenders():
    profiles = load_acquisition_lenders()
    errors   = validate_lender_profiles(profiles)
    return {"profiles": profiles, "validation_errors": errors}


class RecommendRequest(BaseModel):
    purchase_price:  float
    repair_estimate: float
    arv:             float


@app.post("/acquisition/lenders/recommend/{deal_id}")
def recommend(deal_id: int, body: RecommendRequest):
    profiles = load_acquisition_lenders()
    errors   = validate_lender_profiles(profiles)
    if errors:
        raise HTTPException(
            status_code=422,
            detail=f"Lender profile validation failed: {errors}",
        )

    deal_ctx = {
        "purchase_price":  body.purchase_price,
        "repair_estimate": body.repair_estimate,
        "arv":             body.arv,
    }

    recommended = recommend_lender(profiles, deal_ctx)
    if not recommended:
        raise HTTPException(status_code=422, detail="No valid lender profiles available")

    packet = build_lender_packet(deal_ctx, recommended)
    return {
        "recommended_lender": recommended["id"],
        "packet": packet,
        "all_profiles_valid": not errors,
    }


# ── Strategy switch (BRRRR → wholesale) ──────────────────────────────────────

class StrategySwitchRequest(BaseModel):
    reason: str = Field(..., description="Why BRRRR is no longer viable")


@app.post("/acquisition/strategy-switch/{deal_id}")
def propose_strategy_switch(deal_id: int, body: StrategySwitchRequest):
    """
    Propose a BRRRR → wholesale strategy switch.

    Only allowed while the deal is in DUE_DILIGENCE or UNDER_CONTRACT(B) —
    i.e., inside the inspection window.  After that, it requires Gate A
    re-approval per §3 Re-evaluation triggers.
    """
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        _require_brrrr(deal)

        allowed_statuses = {"under_contract", "due_diligence"}
        if deal.get("lead_status") not in allowed_statuses:
            raise HTTPException(
                status_code=422,
                detail=f"Strategy switch only allowed inside inspection window "
                       f"(status={deal.get('lead_status')}). "
                       "Contact the operator to initiate Gate A re-approval.",
            )

        db.execute(
            text("""
                UPDATE deals
                SET strategy_switch_reason = :reason
                WHERE id = :did
            """),
            {"reason": body.reason, "did": deal_id},
        )
        transition_deal(db, deal_id, "strategy_switch", actor="agent_12",
                        reason=body.reason)
        db.commit()

        log_agent_event(
            source_agent="acquisition",
            target_agent="operator",
            payload={"event_type": "STRATEGY_SWITCH_PROPOSED", "deal_id": deal_id,
                     "reason": body.reason},
            response={}, status_code=200,
        )

    return {
        "status": "strategy_switch_pending_gate_a",
        "deal_id": deal_id,
        "reason": body.reason,
        "message": "Gate A re-approval required before switching to wholesale.",
    }


@app.get("/")
def health():
    return {"status": "ok", "agent": "acquisition"}


def main() -> None:
    import uvicorn
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [acquisition] %(message)s",
    )
    uvicorn.run(
        "agents.acquisition.run:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8012")),
        reload=False,
    )


if __name__ == "__main__":
    main()
