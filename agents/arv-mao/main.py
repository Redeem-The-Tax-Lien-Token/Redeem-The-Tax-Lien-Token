"""
ARV/MAO Agent — FastAPI app.

Endpoints:
  GET  /          health check
  POST /run       pull ATTOM comps → auto repair estimate → Claude ARV → MAO
  GET  /version   git commit hash for deploy verification

All /run calls require the X-Internal-Key header.

Repair estimation pipeline (fires only when repair_estimate is NOT supplied):
  1. ATTOM property/detail  → subject_sqft, subject_year_built, attom_condition
  2. Street View metadata   → coverage check (free call)
  3. Street View image      → fetched only when coverage exists and is fresh
  4. Claude vision          → condition + confidence + visible_flags
  5. repair_estimate.estimate() → deterministic tier → dollar amount

When repair_estimate IS supplied manually it overrides the entire pipeline
above (no Street View or vision calls are made — no wasted credits).
"""

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy import text

_here      = Path(__file__).parent
_repo_root = _here.parent.parent
for _p in [str(_here), str(_repo_root)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.db import log_agent_event, session_ctx  # noqa: E402
from agents           import analyze_comps           # noqa: E402
from attom_utils      import get_comps, get_property_detail  # noqa: E402
from repair_estimate  import estimate as estimate_repair      # noqa: E402
from street_view      import check_coverage, fetch_image     # noqa: E402
from condition_vision import read_condition                   # noqa: E402

log = logging.getLogger("arv-mao")
logging.basicConfig(level=logging.INFO)

AGENT_NAME       = "arv-mao"
INTERNAL_API_KEY = os.environ["INTERNAL_API_KEY"]

app = FastAPI(title="ARV/MAO Agent", version="2.0.0")
_key_header = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def _require_key(key: str = Security(_key_header)) -> str:
    if key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Internal-Key")
    return key


# ── Request / Response models ─────────────────────────────────────────────────

class RunRequest(BaseModel):
    address:         str
    city:            str
    state:           str            = "IN"
    zip:             str
    repair_estimate: Optional[float] = Field(
        None,
        description="Manual override. When set, skips Street View and vision.",
    )
    motivation_type: Optional[str]  = Field(
        None,
        description="e.g. tax_delinquent/pre_foreclosure/vacant — floors repair tier at medium",
    )
    lead_id:         Optional[int]  = Field(None, description="Writes result to deals table")


class ArvMaoResponse(BaseModel):
    # ARV
    arv_low:         float
    arv_mid:         float
    arv_high:        float
    arv_confidence:  str
    comps_used:      int
    notes:           str
    # Repair
    repair_estimate: float
    repair_tier:     Optional[str] = None   # None when manually supplied
    repair_basis:    Optional[str] = None   # None when manually supplied
    # Vision
    visible_flags:        Optional[list[str]] = None
    vision_condition:     Optional[str]       = None
    street_view_checked:  bool                = False
    # MAO (computed in Python)
    mao:             float
    # Address echo
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

    # ── 1. ATTOM comps (fatal if none found) ──────────────────────────────
    try:
        comps = get_comps(req.address, req.city, req.state, req.zip)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ATTOM comps error: {exc}")

    if not comps:
        raise HTTPException(
            status_code=422,
            detail="No comparable sales found. Verify address/zip or expand search manually.",
        )

    subject = {"address": req.address, "city": req.city,
               "state": req.state,    "zip":  req.zip}

    # ── 2. Subject property detail (non-fatal) ────────────────────────────
    subject_sqft:       Optional[float] = None
    subject_year_built: Optional[int]   = None
    attom_condition:    Optional[str]   = None

    try:
        address2 = f"{req.city}, {req.state} {req.zip}"
        detail = get_property_detail(req.address, address2)
        if detail:
            building = detail.get("building", {})
            size     = building.get("size", {})
            subject_sqft = (
                size.get("universalsize") or size.get("livingsize")
            )
            if subject_sqft:
                subject_sqft = float(subject_sqft)
            yr = building.get("summary", {}).get("yearbuilt") \
                 or detail.get("summary", {}).get("yearbuilt")
            if yr:
                subject_year_built = int(yr)
            cond = (
                building.get("condition")
                or detail.get("summary", {}).get("condition")
            )
            attom_condition = str(cond) if cond else None
    except Exception as exc:
        log.warning("Property detail fetch failed (non-fatal): %s", exc)

    # ── 3+4. Vision pipeline — skip entirely when override is supplied ────
    vision_condition:  Optional[str]       = None
    vision_confidence: Optional[str]       = None
    visible_flags:     Optional[list[str]] = None
    street_view_checked = False

    if req.repair_estimate is None:
        street_view_checked = True
        cov = check_coverage(req.address, req.city, req.state, req.zip)
        log.info("Street View: %s", cov.get("reason"))

        if cov["has_coverage"] and not cov["is_stale"]:
            img = fetch_image(req.address, req.city, req.state, req.zip)
            if img:
                try:
                    vision = read_condition(img)
                    vision_condition  = vision["condition"]
                    vision_confidence = vision["confidence"]
                    visible_flags     = vision["visible_flags"]
                    log.info("Vision: %s (%s) flags=%s",
                             vision_condition, vision_confidence, visible_flags)
                except Exception as exc:
                    log.warning("Vision read failed (non-fatal): %s", exc)
        else:
            log.info("Street View skipped: %s", cov.get("reason"))

    # ── 5. Claude ARV (comps only — no repair/MAO) ────────────────────────
    try:
        analysis = analyze_comps(subject, comps)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ARV analysis error: {exc}")

    arv_mid = float(analysis.get("arv_mid", 0))

    # ── 6+7. Repair estimate (Python, not Claude) ─────────────────────────
    if req.repair_estimate is not None:
        repair_estimate = float(req.repair_estimate)
        repair_tier     = None
        repair_basis    = None
    else:
        est = estimate_repair(
            arv_mid           = arv_mid,
            sqft              = subject_sqft,
            year_built        = subject_year_built,
            attom_condition   = attom_condition,
            vision_condition  = vision_condition,
            vision_confidence = vision_confidence,
            motivation_type   = req.motivation_type,
        )
        repair_estimate = est["repair_estimate"]
        repair_tier     = est["repair_tier"]
        repair_basis    = est["repair_basis"]

    # ── 8. MAO in Python (never inside Claude) ────────────────────────────
    mao = (arv_mid * 0.70) - repair_estimate - 10_000

    # ── 9. Optional DB write (non-fatal) ──────────────────────────────────
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
                        "arv_mid":         arv_mid,
                        "arv_high":        analysis.get("arv_high"),
                        "arv_confidence":  analysis.get("confidence"),
                        "repair_estimate": repair_estimate,
                        "mao":             mao,
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
            db_warning = f"Deal not saved: {exc}"

    return ArvMaoResponse(
        arv_low              = float(analysis.get("arv_low", 0)),
        arv_mid              = arv_mid,
        arv_high             = float(analysis.get("arv_high", 0)),
        arv_confidence       = analysis.get("confidence", "low"),
        comps_used           = analysis.get("comps_used", len(comps)),
        notes                = analysis.get("notes", ""),
        repair_estimate      = repair_estimate,
        repair_tier          = repair_tier,
        repair_basis         = repair_basis,
        visible_flags        = visible_flags,
        vision_condition     = vision_condition,
        street_view_checked  = street_view_checked,
        mao                  = mao,
        address              = req.address,
        city                 = req.city,
        state                = req.state,
        zip                  = req.zip,
        comps                = comps,
        deal_id              = deal_id,
        db_warning           = db_warning,
    )
