"""
ARV/MAO Agent — FastAPI app.

Endpoints:
  GET  /          health check
  POST /run       pull ATTOM comps → auto repair estimate → Claude ARV → MAO
  POST /underwrite  full §3 strategy engine: wholesale + BRRRR → chosen strategy
  GET  /version   git commit hash for deploy verification

All calls require the X-Internal-Key header.

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

# Strategy engine — imported lazily so the /run endpoint stays fast
from core.strategy.wholesale  import compute_wholesale     # noqa: E402
from core.strategy.brrrr      import compute_brrrr, brrrr_max_price  # noqa: E402
from core.strategy.eligibility import check_wholesale, check_brrrr   # noqa: E402
from core.strategy.offer_policy import select_offer_price            # noqa: E402
from adapters.rent_comps       import manual_entry as rent_manual     # noqa: E402

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


# ── /underwrite — full §3 Strategy Decision Engine ────────────────────────────

class UnderwriteRequest(BaseModel):
    # Property inputs (can come from a prior /run call)
    arv:                  float  = Field(..., description="Mid-point ARV from /run or manual")
    repairs:              float  = Field(..., description="Repair estimate")
    neighborhood_class:   str    = Field(..., description="A | B | C")
    # Rent (manual entry — phase 1; will be fetched automatically in phase 2)
    market_rent:          float  = Field(..., description="Gross monthly market rent (from manual comp review)")
    rent_comp_count:      int    = Field(0,   description="Number of rental comps used")
    rent_confidence:      str    = Field("medium", description="low | medium | high")
    rent_notes:           str    = Field("",  description="Provenance for the rent estimate")
    # Deal context
    available_buyers:     int    = Field(5,   description="Active qualified buyers matching zip/price/tier")
    seller_timeline_days: int    = Field(30,  description="Days seller needs to close")
    available_capital:    float  = Field(0.0, description="Capital available for BRRRR deployment")
    active_projects:      int    = Field(0,   description="Active BRRRR projects count")
    property_type:        str    = Field("SFR", description="SFR | 2-unit | 3-unit | 4-unit")
    repair_tier:          str    = Field("MEDIUM", description="LIGHT | MEDIUM | HEAVY | GUT")
    # Optional
    deal_id:              Optional[int] = None
    lead_id:              Optional[int] = None


class UnderwriteResponse(BaseModel):
    # Chosen strategy
    strategy:             str             # "wholesale" | "brrrr" | "nurture"
    offer_price:          Optional[float]
    fallback_fee:         Optional[float]
    no_exit_if_funding_fails: bool
    # Wholesale
    wholesale_offer_price:  float
    wholesale_fee:          float
    wholesale_eligible:     bool
    wholesale_reasons:      list[str]
    # BRRRR (None when brrrr_enabled=False or ineligible)
    brrrr_max_price:        Optional[float]
    brrrr_cash_left_in:     Optional[float]
    brrrr_dscr:             Optional[float]
    brrrr_cash_flow:        Optional[float]
    brrrr_refi_binding:     Optional[str]
    brrrr_eligible:         bool
    brrrr_reasons:          list[str]
    # Rent comps used
    rent_comps_source:      str
    rent_comp_count:        int
    rent_confidence:        str
    # Gate A flags
    gate_a_required:        bool
    risk_flags:             list[str]


@app.post("/underwrite", response_model=UnderwriteResponse)
def underwrite(req: UnderwriteRequest, _: str = Depends(_require_key)):
    """
    Run the §3 Strategy Decision Engine.

    Produces a full wholesale case and (if enabled) BRRRR case, applies
    eligibility gates, picks the strategy, and returns the Gate A packet.

    market_rent must come from a manual_entry() call (phase 1) or a real
    rent_comps API call (phase 2). Pass rent_comp_count=0 and
    rent_confidence="low" if you have not reviewed comps yet — this will
    fail the BRRRR eligibility gate and force wholesale evaluation only.
    """
    risk_flags: list[str] = []

    # ── Rent comps (manual phase 1) ───────────────────────────────────────
    rent_result = rent_manual(
        market_rent  = req.market_rent,
        comp_count   = req.rent_comp_count,
        confidence   = req.rent_confidence,
        notes        = req.rent_notes,
    )

    # ── Wholesale case ────────────────────────────────────────────────────
    w_case  = compute_wholesale(
        arv                = req.arv,
        repairs            = req.repairs,
        neighborhood_class = req.neighborhood_class,
    )
    w_elig  = check_wholesale(
        case                 = w_case,
        available_buyers     = req.available_buyers,
        seller_timeline_days = req.seller_timeline_days,
    )

    # ── BRRRR case (skip when capital = 0 or brrrr_enabled=False) ─────────
    b_max_price  = None
    b_cash_left  = None
    b_dscr       = None
    b_cash_flow  = None
    b_refi_bind  = None
    b_eligible   = False
    b_reasons: list[str] = []

    try:
        from core.strategy._config import load as _load_cfg
        cfg = _load_cfg()
        brrrr_on = cfg.get("brrrr", {}).get("enabled", False)
    except Exception:
        brrrr_on = False

    if brrrr_on and req.available_capital > 0:
        try:
            b_max_price = brrrr_max_price(
                repairs     = req.repairs,
                arv         = req.arv,
                market_rent = rent_result.market_rent,
                rehab_tier  = req.repair_tier,
            )
            if b_max_price is not None and b_max_price > 0:
                b_case_at_max = compute_brrrr(
                    purchase    = b_max_price,
                    repairs     = req.repairs,
                    arv         = req.arv,
                    market_rent = rent_result.market_rent,
                    rehab_tier  = req.repair_tier,
                )
                b_cash_left = b_case_at_max.cash_left_in
                b_dscr      = b_case_at_max.dscr
                b_cash_flow = b_case_at_max.cash_flow
                b_refi_bind = b_case_at_max.refi_binding

                b_elig = check_brrrr(
                    case              = b_case_at_max,
                    available_capital = req.available_capital,
                    active_projects   = req.active_projects,
                    property_type     = req.property_type,
                )
                b_eligible = b_elig.all_pass
                b_reasons  = list(b_elig.reasons)
        except Exception as exc:
            log.warning("BRRRR engine error (non-fatal): %s", exc)
            b_reasons = [f"ENGINE_ERROR: {exc}"]
            risk_flags.append(f"BRRRR engine error: {exc}")

    # ── Strategy selection (§3 Step 3–4) ──────────────────────────────────
    offer_price: Optional[float] = None
    strategy = "nurture"

    if w_elig.all_pass and not b_eligible:
        strategy    = "wholesale"
        offer_price = w_case.offer_price
    elif b_eligible and not w_elig.all_pass:
        strategy    = "brrrr"
        offer_price = b_max_price
    elif w_elig.all_pass and b_eligible:
        # Both eligible — take higher offer (highest_eligible policy)
        if b_max_price is not None and b_max_price >= w_case.offer_price:
            strategy    = "brrrr"
            offer_price = b_max_price
        else:
            strategy    = "wholesale"
            offer_price = w_case.offer_price
    # else: neither → nurture

    # ── Fallback fee check ────────────────────────────────────────────────
    fallback_fee = None
    no_exit      = False
    if strategy == "brrrr" and offer_price is not None:
        fallback_fee = (req.arv * w_case.discount_rate) - req.repairs - offer_price
        if fallback_fee < w_case.fee_at_offer or not w_elig.all_pass:
            no_exit = True
            risk_flags.append("NO_EXIT_IF_FUNDING_FAILS")

    # ── Risk flags ────────────────────────────────────────────────────────
    if rent_result.comp_count < 2:
        risk_flags.append("LOW_RENT_COMP_COUNT")
    if rent_result.confidence == "low":
        risk_flags.append("LOW_RENT_CONFIDENCE")
    if w_case.offer_price == 0:
        risk_flags.append("ZERO_WHOLESALE_OFFER")

    gate_a = offer_price is not None and offer_price > 0

    log_agent_event(
        source_agent = AGENT_NAME,
        target_agent = "strategy_engine",
        payload      = {
            "deal_id":   req.deal_id,
            "arv":       req.arv,
            "repairs":   req.repairs,
            "strategy":  strategy,
        },
        response     = {"offer_price": offer_price, "risk_flags": risk_flags},
        status_code  = 200,
    )

    return UnderwriteResponse(
        strategy                   = strategy,
        offer_price                = offer_price,
        fallback_fee               = fallback_fee,
        no_exit_if_funding_fails   = no_exit,
        wholesale_offer_price      = w_case.offer_price,
        wholesale_fee              = w_case.fee_at_offer,
        wholesale_eligible         = w_elig.all_pass,
        wholesale_reasons          = list(w_elig.reasons),
        brrrr_max_price            = b_max_price,
        brrrr_cash_left_in         = b_cash_left,
        brrrr_dscr                 = b_dscr,
        brrrr_cash_flow            = b_cash_flow,
        brrrr_refi_binding         = b_refi_bind,
        brrrr_eligible             = b_eligible,
        brrrr_reasons              = b_reasons,
        rent_comps_source          = rent_result.source,
        rent_comp_count            = rent_result.comp_count,
        rent_confidence            = rent_result.confidence,
        gate_a_required            = gate_a,
        risk_flags                 = risk_flags,
    )
