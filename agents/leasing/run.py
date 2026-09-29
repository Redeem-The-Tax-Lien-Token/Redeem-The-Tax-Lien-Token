"""
Agent 14 — Leasing (FastAPI service).

Endpoints:
  POST /leasing/listing/{deal_id}         — post rental listing (after FH linter)
  DELETE /leasing/listing/{deal_id}       — remove listing
  POST /leasing/application/{deal_id}     — record + screen a tenant application
  GET  /leasing/applications/{deal_id}    — list applications
  POST /leasing/lease/{deal_id}           — create lease and advance to LEASED
  GET  /leasing/lease/{deal_id}           — get current lease
  POST /leasing/move-in/{deal_id}         — record move-in inspection
  POST /leasing/renewal/{deal_id}         — initiate lease renewal
  GET  /                                  — health check

Contract (§7, Agent 14):
  - Only for properties the entity OWNS (BRRRR track, rehab_complete or later).
  - All listing text passes fair-housing linter before syndication.
  - Screening criteria are written, uniform, and applied identically to every
    applicant by deterministic Python — not the LLM.
  - Protected characteristics are never evaluated, collected, or logged.
  - SYSTEM_MODE=dry_run → listing and screening calls go to log only.
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
import adapters.listing_syndication as syndication

from .fair_housing import lint_listing, guard_application_fields
from .screening import evaluate_application, CRITERIA_VERSION

log = logging.getLogger(__name__)

app = FastAPI(title="Agent 14 — Leasing", version="0.1.0")


def _get_deal(db, deal_id: int) -> dict:
    row = db.execute(
        text("""
            SELECT d.id, d.strategy, d.rent_actual, d.listing_id,
                   l.id AS lead_id, l.status AS lead_status,
                   l.address, l.city, l.owner_name
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
        raise HTTPException(status_code=422, detail="Leasing is only for BRRRR deals")


# ── Listing ───────────────────────────────────────────────────────────────────

class ListingCreate(BaseModel):
    rent_amount:       float
    bedrooms:          int
    bathrooms:         float
    sqft:              int | None = None
    description:       str
    available_date:    str   # ISO date
    pets_allowed:      bool = False
    utilities_included: list[str] = []


@app.post("/leasing/listing/{deal_id}")
def post_listing(deal_id: int, body: ListingCreate):
    """Post a rental listing — runs fair-housing linter first (fail-closed)."""
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        _require_brrrr(deal)

        lint = lint_listing(body.description)
        if not lint.ok:
            raise HTTPException(
                status_code=422,
                detail={"message": "Listing text failed fair-housing linter",
                        "flags": lint.flags},
            )

        listing = {
            "deal_id":     deal_id,
            "address":     deal["address"],
            "city":        deal["city"],
            "rent":        body.rent_amount,
            "bedrooms":    body.bedrooms,
            "bathrooms":   body.bathrooms,
            "description": body.description,
        }
        result = syndication.post_listing(listing)

        db.execute(
            text("""
                UPDATE deals
                SET listing_id = :lid, rent_actual = :rent, listed_for_rent_at = NOW()
                WHERE id = :did
            """),
            {"lid": result.listing_id, "rent": body.rent_amount, "did": deal_id},
        )
        if deal.get("lead_status") == "rent_ready":
            transition_deal(db, deal_id, "listed_for_rent", actor="agent_14",
                            reason="Rental listing posted")
        db.commit()

        log_agent_event(
            source_agent="leasing",
            target_agent="operator",
            payload={"event_type": "LISTING_POSTED", "deal_id": deal_id,
                     "listing_id": result.listing_id, "rent": body.rent_amount},
            response={}, status_code=200,
        )

    return {"listing_id": result.listing_id, "status": result.status}


@app.delete("/leasing/listing/{deal_id}")
def remove_listing(deal_id: int):
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        listing_id = deal.get("listing_id")
        if not listing_id:
            raise HTTPException(status_code=404, detail="No active listing for this deal")
        syndication.remove_listing(listing_id)
        db.execute(
            text("UPDATE deals SET listing_id = NULL WHERE id = :did"),
            {"did": deal_id},
        )
        db.commit()
    return {"status": "removed", "listing_id": listing_id}


# ── Applications ──────────────────────────────────────────────────────────────

class ApplicationCreate(BaseModel):
    applicant_name:          str
    monthly_income:          float
    credit_score:            int | None = None
    debt_to_income:          float | None = None
    eviction_history:        bool = False
    prior_landlord_reference: bool = False


@app.post("/leasing/application/{deal_id}")
def submit_application(deal_id: int, body: ApplicationCreate):
    """
    Record and evaluate a tenant application.

    Protected characteristics are never accepted.  Evaluation is deterministic
    Python only — the LLM does not participate.
    """
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        _require_brrrr(deal)

        app_dict = body.model_dump()
        guard_application_fields(app_dict)   # raises if protected field present

        monthly_rent = float(deal.get("rent_actual") or 0)
        if monthly_rent <= 0:
            raise HTTPException(status_code=422,
                                detail="Set rent_actual on deal before accepting applications")

        decision = evaluate_application(app_dict, monthly_rent)

        app_id = db.execute(
            text("""
                INSERT INTO tenant_applications
                    (deal_id, applicant_name, screening_status, decision,
                     denial_reasons, criteria_version)
                VALUES (:did, :name, :ss, :dec, :reasons, :ver)
                RETURNING id
            """),
            {
                "did":     deal_id,
                "name":    body.applicant_name,
                "ss":      decision.decision,
                "dec":     decision.decision,
                "reasons": decision.reasons or [],
                "ver":     CRITERIA_VERSION,
            },
        ).scalar()
        db.commit()

        log_agent_event(
            source_agent="leasing",
            target_agent="operator",
            payload={"event_type": "APPLICATION_RECEIVED", "deal_id": deal_id,
                     "application_id": app_id, "decision": decision.decision},
            response={}, status_code=200,
        )

    return {
        "application_id": app_id,
        "decision":       decision.decision,
        "reasons":        decision.reasons,
        "criteria_version": CRITERIA_VERSION,
    }


@app.get("/leasing/applications/{deal_id}")
def list_applications(deal_id: int):
    with session_ctx() as db:
        _get_deal(db, deal_id)
        rows = db.execute(
            text("""
                SELECT id, applicant_name, decision, denial_reasons,
                       criteria_version, applied_at
                FROM tenant_applications
                WHERE deal_id = :did
                ORDER BY applied_at DESC
            """),
            {"did": deal_id},
        ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Lease ─────────────────────────────────────────────────────────────────────

class LeaseCreate(BaseModel):
    tenant_name:      str
    tenant_email:     str | None = None
    tenant_phone:     str | None = None
    start_date:       str   # ISO date
    end_date:         str   # ISO date
    rent_monthly:     float
    security_deposit: float


@app.post("/leasing/lease/{deal_id}")
def create_lease(deal_id: int, body: LeaseCreate):
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)
        _require_brrrr(deal)

        lease_id = db.execute(
            text("""
                INSERT INTO leases
                    (deal_id, tenant_name, tenant_email, tenant_phone,
                     start_date, end_date, rent_monthly, security_deposit, status)
                VALUES (:did, :name, :email, :phone, :sd, :ed, :rent, :dep, 'active')
                RETURNING id
            """),
            {
                "did":   deal_id,
                "name":  body.tenant_name,
                "email": body.tenant_email,
                "phone": body.tenant_phone,
                "sd":    body.start_date,
                "ed":    body.end_date,
                "rent":  body.rent_monthly,
                "dep":   body.security_deposit,
            },
        ).scalar()

        db.execute(
            text("""
                UPDATE deals
                SET leased_at = NOW(), rent_actual = :rent
                WHERE id = :did
            """),
            {"rent": body.rent_monthly, "did": deal_id},
        )
        transition_deal(db, deal_id, "leased", actor="agent_14",
                        reason=f"Lease signed with {body.tenant_name}")
        db.commit()

        log_agent_event(
            source_agent="leasing",
            target_agent="refinance",
            payload={"event_type": "LEASED", "deal_id": deal_id,
                     "lease_id": lease_id, "rent": body.rent_monthly},
            response={}, status_code=200,
        )

    return {"lease_id": lease_id, "status": "leased"}


@app.get("/leasing/lease/{deal_id}")
def get_lease(deal_id: int):
    with session_ctx() as db:
        _get_deal(db, deal_id)
        row = db.execute(
            text("""
                SELECT * FROM leases
                WHERE deal_id = :did AND status = 'active'
                ORDER BY created_at DESC LIMIT 1
            """),
            {"did": deal_id},
        ).fetchone()
    return dict(row._mapping) if row else {"lease": None}


# ── Move-in inspection ────────────────────────────────────────────────────────

@app.post("/leasing/move-in/{deal_id}")
def record_move_in(deal_id: int):
    """Record that move-in inspection has been completed."""
    with session_ctx() as db:
        _get_deal(db, deal_id)
        db.execute(
            text("""
                UPDATE leases SET move_in_inspection_at = NOW()
                WHERE deal_id = :did AND status = 'active'
            """),
            {"did": deal_id},
        )
        db.commit()
    return {"status": "move_in_recorded"}


# ── Lease renewal ─────────────────────────────────────────────────────────────

class RenewalCreate(BaseModel):
    new_end_date:    str
    new_rent:        float


@app.post("/leasing/renewal/{deal_id}")
def initiate_renewal(deal_id: int, body: RenewalCreate):
    with session_ctx() as db:
        _get_deal(db, deal_id)
        updated = db.execute(
            text("""
                UPDATE leases
                SET end_date = :ed, rent_monthly = :rent, updated_at = NOW()
                WHERE deal_id = :did AND status = 'active'
            """),
            {"ed": body.new_end_date, "rent": body.new_rent, "did": deal_id},
        ).rowcount
        if not updated:
            raise HTTPException(status_code=404, detail="No active lease found")
        db.execute(
            text("UPDATE deals SET rent_actual = :rent WHERE id = :did"),
            {"rent": body.new_rent, "did": deal_id},
        )
        db.commit()
    return {"status": "renewed", "new_end_date": body.new_end_date, "new_rent": body.new_rent}


@app.get("/")
def health():
    return {"status": "ok", "agent": "leasing"}


def main() -> None:
    import uvicorn
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [leasing] %(message)s",
    )
    uvicorn.run(
        "agents.leasing.run:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8014")),
        reload=False,
    )


if __name__ == "__main__":
    main()
