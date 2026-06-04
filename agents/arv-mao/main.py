"""
ARV/MAO Agent — FastAPI app.

Endpoints:
  GET  /          health check
  POST /run       pull ATTOM comps → Claude ARV/MAO analysis → optional DB write
  GET  /version   git commit hash for deploy verification

All /run calls require the X-Internal-Key header.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy import text

# Support both monorepo dev (shared/ at repo root) and Replit deploy
# (shared/ copied into this folder by build_repl.sh).
_here = Path(__file__).parent
_repo_root = _here.parent.parent
for _p in [str(_here), str(_repo_root)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.db import log_agent_event, session_ctx  # noqa: E402
from agents import analyze_comps                      # noqa: E402
from attom_utils import get_comps                     # noqa: E402

# ── App setup ─────────────────────────────────────────────────────────────────

AGENT_NAME       = "arv-mao"
INTERNAL_API_KEY = os.environ["INTERNAL_API_KEY"]

app = FastAPI(title="ARV/MAO Agent", version="1.0.0")
_key_header = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def _require_key(key: str = Security(_key_header)) -> str:
    if key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Internal-Key")
    return key


# ── Request / Response models ─────────────────────────────────────────────────

class RunRequest(BaseModel):
    address:          str
    city:             str
    state:            str   = "IN"
    zip:              str
    repair_estimate:  float = Field(..., gt=0, description="Estimated repair cost in dollars")
    lead_id:          Optional[int] = Field(None, description="If set, writes result to deals table")


class ArvMaoResponse(BaseModel):
    arv_low:         float
    arv_mid:         float
    arv_high:        float
    arv_confidence:  str
    repair_estimate: float
    mao:             float
    comps_used:      int
    notes:           str
    address:         str
    city:            str
    state:           str
    zip:             str
    comps:           list[dict]
    deal_id:         Optional[int] = None
    db_warning:      Optional[str] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def health_check():
    return {"status": "ok", "agent": AGENT_NAME}


@app.get("/version")
def version():
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=str(_repo_root),
        ).decode().strip()
    except Exception:
        commit = "unknown"
    return {"agent": AGENT_NAME, "commit": commit}


@app.post("/run", response_model=ArvMaoResponse)
def run(req: RunRequest, _: str = Depends(_require_key)):
    # ── 1. Pull comps from ATTOM ───────────────────────────────────────────
    try:
        comps = get_comps(req.address, req.city, req.state, req.zip)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ATTOM error: {exc}")

    if not comps:
        raise HTTPException(
            status_code=422,
            detail=(
                "No comparable sales found. "
                "Verify address/zip or expand search manually."
            ),
        )

    subject = {
        "address": req.address,
        "city":    req.city,
        "state":   req.state,
        "zip":     req.zip,
    }

    # ── 2. Claude ARV/MAO analysis ────────────────────────────────────────
    try:
        analysis = analyze_comps(subject, comps, req.repair_estimate)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Analysis error: {exc}")

    # ── 3. Optional: persist to deals table ───────────────────────────────
    deal_id    = None
    db_warning = None

    if req.lead_id:
        try:
            with session_ctx() as db:
                row = db.execute(
                    text("""
                        INSERT INTO deals
                            (lead_id, arv_low, arv_mid, arv_high, arv_confidence,
                             repair_estimate, mao, status)
                        VALUES
                            (:lead_id, :arv_low, :arv_mid, :arv_high, :arv_confidence,
                             :repair_estimate, :mao, 'new')
                        RETURNING id
                    """),
                    {
                        "lead_id":         req.lead_id,
                        "arv_low":         analysis.get("arv_low"),
                        "arv_mid":         analysis.get("arv_mid"),
                        "arv_high":        analysis.get("arv_high"),
                        "arv_confidence":  analysis.get("confidence"),
                        "repair_estimate": req.repair_estimate,
                        "mao":             analysis.get("mao"),
                    },
                ).fetchone()
                deal_id = row[0] if row else None

            log_agent_event(
                source_agent=AGENT_NAME,
                target_agent="database",
                payload={"lead_id": req.lead_id, "action": "insert_deal"},
                response={"deal_id": deal_id},
                status_code=200,
            )
        except Exception as exc:
            # DB write failure is non-fatal — return the analysis anyway
            db_warning = f"Deal not saved to DB: {exc}"

    return ArvMaoResponse(
        arv_low         = analysis.get("arv_low", 0),
        arv_mid         = analysis.get("arv_mid", 0),
        arv_high        = analysis.get("arv_high", 0),
        arv_confidence  = analysis.get("confidence", "low"),
        repair_estimate = req.repair_estimate,
        mao             = analysis.get("mao", 0),
        comps_used      = analysis.get("comps_used", len(comps)),
        notes           = analysis.get("notes", ""),
        address         = req.address,
        city            = req.city,
        state           = req.state,
        zip             = req.zip,
        comps           = comps,
        deal_id         = deal_id,
        db_warning      = db_warning,
    )
