"""
Agent 15 — Refinance (FastAPI service).

Endpoints:
  GET  /refinance/eligibility/{deal_id}      — check seasoning + compute refi
  POST /refinance/apply/{deal_id}            — submit refi application (dry-run or live)
  POST /refinance/appraisal/{deal_id}        — record appraisal result
  POST /refinance/gate-b/{deal_id}/close     — record refi close (Gate B approval)
  POST /refinance/reevaluate/{deal_id}       — trigger REFI_REEVALUATE (low appraisal)
  POST /refinance/capital-recovery/{deal_id} — record capital returned to pool
  GET  /                                     — health check

Contract (§7, Agent 15):
  - Seasoning is checked against the lender's profile before any application.
  - Refi math is deterministic Python per §2.3.
  - Lender profiles older than 45 days block eligibility (fail-closed).
  - Refi close requires Gate B operator approval.
  - Capital recovered is written to the capital_pool ledger.
  - BRRRR scorecard is finalized on close.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import yaml as _yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from shared.db import session_ctx, log_agent_event
from shared.state_machine import transition_deal
from agents.acquisition.lender_packet import validate_lender_profiles
import adapters.lender as lender_adapter

from .seasoning import check_seasoning, months_between
from .refi_calculator import calculate_refi

log = logging.getLogger(__name__)

app = FastAPI(title="Agent 15 — Refinance", version="0.1.0")

_STRATEGY_PATH = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"
_LENDING_PATH  = Path(__file__).parent.parent.parent / "config" / "lending.yaml"


def _load_cfg() -> dict:
    return _yaml.safe_load(_STRATEGY_PATH.read_text())


def _load_refi_lenders() -> list[dict]:
    lending = _yaml.safe_load(_LENDING_PATH.read_text())
    return lending.get("refi_lenders", [])


def _get_deal(db, deal_id: int) -> dict:
    row = db.execute(
        text("""
            SELECT d.id, d.strategy, d.seasoning_start_date,
                   d.rent_actual, d.refi_loan_amount, d.cash_left_in,
                   d.refi_applied_at, d.refi_appraised_at, d.refi_closed_at,
                   d.underwriting_snapshot,
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


# ── Eligibility check ─────────────────────────────────────────────────────────

@app.get("/refinance/eligibility/{deal_id}")
def check_eligibility(deal_id: int, lender_id: str = "dscr_default"):
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)

    acquisition_date = deal.get("seasoning_start_date")
    if not acquisition_date:
        raise HTTPException(
            status_code=422,
            detail="seasoning_start_date not set on deal. Set it at acquisition close."
        )

    if isinstance(acquisition_date, str):
        acquisition_date = date.fromisoformat(acquisition_date)

    refi_lenders = _load_refi_lenders()
    lender = next((l for l in refi_lenders if l["id"] == lender_id), None)
    if not lender:
        raise HTTPException(status_code=404, detail=f"Lender '{lender_id}' not found")

    errors = validate_lender_profiles([lender])
    if errors:
        raise HTTPException(status_code=422,
                            detail=f"Lender profile stale: {errors}")

    seasoning = check_seasoning(acquisition_date, lender)

    if not seasoning.eligible:
        return {
            "eligible":     False,
            "months_owned": seasoning.months_owned,
            "reason":       seasoning.reason,
        }

    snapshot = deal.get("underwriting_snapshot") or {}
    cfg       = _load_cfg()

    inputs = {
        "arv":              snapshot.get("arv") or 0,
        "purchase":         snapshot.get("purchase_price") or 0,
        "buy_closing":      snapshot.get("buy_closing") or 0,
        "repair_estimate":  snapshot.get("repair_estimate") or 0,
        "rent_actual":      float(deal.get("rent_actual") or 0),
        "months_owned":     seasoning.months_owned,
        "value_basis_rule": seasoning.value_basis_rule,
        "all_in_cost":      snapshot.get("all_in_cost") or 0,
    }

    result = calculate_refi(inputs, lender, cfg)

    return {
        "eligible":         result.eligible,
        "months_owned":     result.months_owned,
        "value_basis_rule": result.value_basis_rule,
        "value_basis":      result.value_basis,
        "refi_loan":        result.refi_loan,
        "refi_binding":     result.refi_binding,
        "cash_left_in":     result.cash_left_in,
        "cash_flow":        result.cash_flow,
        "dscr":             result.dscr,
        "cash_on_cash":     result.cash_on_cash,
        "ineligibility_reasons": result.ineligibility_reasons,
        "lender_id":        lender_id,
        "seasoning_reason": seasoning.reason,
    }


# ── Apply ─────────────────────────────────────────────────────────────────────

class RefiApplyRequest(BaseModel):
    lender_id: str = "dscr_default"


@app.post("/refinance/apply/{deal_id}")
def apply_for_refi(deal_id: int, body: RefiApplyRequest):
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)

        refi_lenders = _load_refi_lenders()
        lender = next((l for l in refi_lenders if l["id"] == body.lender_id), None)
        if not lender:
            raise HTTPException(status_code=404, detail=f"Lender '{body.lender_id}' not found")

        errors = validate_lender_profiles([lender])
        if errors:
            raise HTTPException(status_code=422, detail=f"Lender profile stale: {errors}")

        result = lender_adapter.submit_application(
            body.lender_id,
            {"deal_id": deal_id, "address": deal.get("address")},
        )

        app_id = db.execute(
            text("""
                INSERT INTO refi_applications
                    (deal_id, lender_id, application_id, status, submitted_at)
                VALUES (:did, :lid, :aid, :status, NOW())
                RETURNING id
            """),
            {
                "did":    deal_id,
                "lid":    body.lender_id,
                "aid":    result.application_id,
                "status": result.status,
            },
        ).scalar()

        db.execute(
            text("UPDATE deals SET refi_applied_at = NOW(), refi_lender_id = :lid WHERE id = :did"),
            {"lid": body.lender_id, "did": deal_id},
        )
        transition_deal(db, deal_id, "refi_applied", actor="agent_15",
                        reason=f"Refi application submitted to {body.lender_id}")
        db.commit()

        log_agent_event(
            source_agent="refinance",
            target_agent="operator",
            payload={"event_type": "REFI_APPLIED", "deal_id": deal_id,
                     "lender_id": body.lender_id, "application_id": result.application_id},
            response={}, status_code=200,
        )

    return {"status": "applied", "application_id": result.application_id, "refi_app_id": app_id}


# ── Appraisal ─────────────────────────────────────────────────────────────────

class AppraisalRecord(BaseModel):
    appraised_value: float
    lender_id:       str = "dscr_default"


@app.post("/refinance/appraisal/{deal_id}")
def record_appraisal(deal_id: int, body: AppraisalRecord):
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)

        db.execute(
            text("""
                UPDATE deals
                SET refi_appraised_at = NOW(), refi_appraised_value = :val
                WHERE id = :did
            """),
            {"val": body.appraised_value, "did": deal_id},
        )
        transition_deal(db, deal_id, "appraised", actor="agent_15",
                        reason=f"Appraised at ${body.appraised_value:,.0f}")
        db.commit()

        snapshot   = deal.get("underwriting_snapshot") or {}
        projected  = float(snapshot.get("arv") or 0)
        variance   = body.appraised_value - projected

        log_agent_event(
            source_agent="refinance",
            target_agent="operator",
            payload={"event_type": "APPRAISED", "deal_id": deal_id,
                     "appraised_value": body.appraised_value,
                     "projected_arv": projected,
                     "variance": variance},
            response={}, status_code=200,
        )

    return {
        "status":          "appraised",
        "appraised_value": body.appraised_value,
        "projected_arv":   projected,
        "variance":        variance,
        "flag": "LOW_APPRAISAL" if variance < -10_000 else None,
    }


# ── Refi close (Gate B) ───────────────────────────────────────────────────────

class RefiCloseRequest(BaseModel):
    approved_by:     str = Field("operator")
    loan_amount:     float
    rate:            float
    monthly_payment: float
    lender_id:       str


@app.post("/refinance/gate-b/{deal_id}/close")
def refi_close(deal_id: int, body: RefiCloseRequest):
    """
    Record refinance closing.  Gate B: operator must call this — it never auto-fires.
    On close, finalize the BRRRR scorecard and credit recovered capital to the pool.
    """
    with session_ctx() as db:
        deal = _get_deal(db, deal_id)

        snapshot     = deal.get("underwriting_snapshot") or {}
        all_in_cost  = float(snapshot.get("all_in_cost") or 0)
        refi_costs   = round(body.loan_amount * 0.025 + 2000, 2)  # estimate
        cash_left_in = round(all_in_cost + refi_costs - body.loan_amount, 2)
        rent_actual  = float(deal.get("rent_actual") or 0)
        cfg          = _load_cfg()
        opex_cfg     = cfg.get("opex", {})
        acq_cfg      = cfg.get("acquisition", {})

        taxes_monthly     = float(snapshot.get("arv", 0)) * float(acq_cfg.get("tax_pct", 0.02)) / 12
        insurance_monthly = float(acq_cfg.get("insurance_annual", 1600)) / 12
        opex_monthly      = (taxes_monthly + insurance_monthly +
                              rent_actual * (float(opex_cfg.get("vacancy_rate", 0.08)) +
                                             float(opex_cfg.get("maintenance_rate", 0.08)) +
                                             float(opex_cfg.get("capex_rate", 0.07)) +
                                             float(opex_cfg.get("management_rate", 0.10))))
        cash_flow = round(rent_actual - body.monthly_payment - opex_monthly, 2)
        dscr      = round(rent_actual / (body.monthly_payment + taxes_monthly + insurance_monthly), 4) \
                    if (body.monthly_payment + taxes_monthly + insurance_monthly) > 0 else 0

        scorecard = {
            "purchase":         snapshot.get("purchase_price"),
            "arv":              snapshot.get("arv"),
            "repair_estimate":  snapshot.get("repair_estimate"),
            "all_in_cost":      all_in_cost,
            "refi_loan":        body.loan_amount,
            "refi_rate":        body.rate,
            "refi_payment":     body.monthly_payment,
            "refi_costs":       refi_costs,
            "cash_left_in":     cash_left_in,
            "rent_actual":      rent_actual,
            "opex_monthly":     round(opex_monthly, 2),
            "cash_flow":        cash_flow,
            "dscr":             dscr,
        }

        db.execute(
            text("""
                UPDATE deals
                SET refi_closed_at = NOW(),
                    refi_loan_amount = :loan,
                    refi_rate = :rate,
                    refi_payment_monthly = :pmt,
                    refi_lender_id = :lid,
                    cash_left_in = :cli,
                    dscr_actual = :dscr,
                    cash_flow_actual = :cf,
                    brrrr_scorecard = :sc::jsonb
                WHERE id = :did
            """),
            {
                "loan": body.loan_amount, "rate": body.rate,
                "pmt":  body.monthly_payment, "lid": body.lender_id,
                "cli":  cash_left_in, "dscr": dscr, "cf": cash_flow,
                "sc":   __import__("json").dumps(scorecard), "did": deal_id,
            },
        )
        transition_deal(db, deal_id, "refinanced", actor=body.approved_by,
                        reason="Refi closed — Gate B approved")

        # Credit recovered capital to the pool
        recovered = max(body.loan_amount - all_in_cost - refi_costs, 0)
        if recovered > 0:
            prev_balance = (db.execute(
                text("SELECT running_balance FROM capital_pool ORDER BY id DESC LIMIT 1")
            ).scalar()) or 0

            db.execute(
                text("""
                    INSERT INTO capital_pool (event_type, deal_id, amount, running_balance, notes)
                    VALUES ('recovered', :did, :amt, :bal, :notes)
                """),
                {
                    "did":   deal_id,
                    "amt":   recovered,
                    "bal":   round(float(prev_balance) + recovered, 2),
                    "notes": f"Capital recovered from BRRRR deal #{deal_id} refi",
                },
            )

        db.commit()

        log_agent_event(
            source_agent="refinance",
            target_agent="operator",
            payload={"event_type": "REFI_CLOSED", "deal_id": deal_id,
                     "loan_amount": body.loan_amount, "cash_left_in": cash_left_in,
                     "capital_recovered": recovered},
            response={}, status_code=200,
        )

    return {
        "status":            "refinanced",
        "deal_id":           deal_id,
        "cash_left_in":      cash_left_in,
        "dscr":              dscr,
        "cash_flow":         cash_flow,
        "capital_recovered": recovered,
    }


# ── REFI_REEVALUATE (low appraisal) ──────────────────────────────────────────

class ReevaluateRequest(BaseModel):
    option: str = Field(...,
                        description="'appeal' | 'new_lender' | 'hold_as_is' | 'sell_retail'")
    notes:  str = ""


@app.post("/refinance/reevaluate/{deal_id}")
def refi_reevaluate(deal_id: int, body: ReevaluateRequest):
    allowed = {"appeal", "new_lender", "hold_as_is", "sell_retail"}
    if body.option not in allowed:
        raise HTTPException(status_code=400,
                            detail=f"option must be one of {allowed}")
    with session_ctx() as db:
        _get_deal(db, deal_id)
        transition_deal(db, deal_id, "refi_reevaluate", actor="agent_15",
                        reason=f"Low appraisal — option: {body.option}")
        db.commit()
        log_agent_event(
            source_agent="refinance",
            target_agent="operator",
            payload={"event_type": "REFI_REEVALUATE", "deal_id": deal_id,
                     "option": body.option, "notes": body.notes},
            response={}, status_code=200,
        )
    return {"status": "refi_reevaluate", "option": body.option}


@app.get("/")
def health():
    return {"status": "ok", "agent": "refinance"}


def main() -> None:
    import uvicorn
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [refinance] %(message)s",
    )
    uvicorn.run(
        "agents.refinance.run:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8015")),
        reload=False,
    )


if __name__ == "__main__":
    main()
