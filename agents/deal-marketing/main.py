"""
Deal Marketing Agent — FastAPI app.

Triggered when a PSA is signed. Finds the matching deal and lead, segments
the buyer database by buy-box and area, DNC-scrubs buyer phones, and blasts
the deal via SMS. Never exposes the street address in the blast — buyers who
reply YES get it through a follow-up.

Endpoints:
  GET  /      health check
  POST /run   blast a deal to matched buyers (X-Internal-Key required)
  GET  /version
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

# ── Import path ───────────────────────────────────────────────────────────────
_here      = Path(__file__).parent
_repo_root = _here.parent.parent
for _p in [str(_here), str(_repo_root)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.db import log_agent_event, session_ctx  # noqa: E402
from tracerfy_utils import scrub_phones              # noqa: E402
from twilio_utils import (                           # noqa: E402
    build_deal_blast,
    is_within_calling_hours,
    send_sms,
)

# ── Config ────────────────────────────────────────────────────────────────────

AGENT_NAME       = "deal-marketing"
INTERNAL_API_KEY = os.environ["INTERNAL_API_KEY"]

log = logging.getLogger(AGENT_NAME)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Deal Marketing Agent", version="1.0.0")
_key_header = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def _require_key(key: str = Security(_key_header)) -> str:
    if key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Internal-Key")
    return key


# ── Models ────────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    deal_id:     int
    max_buyers:  int  = Field(default=100, ge=1, le=500)
    max_tier:    int  = Field(
        default=3, ge=1, le=3,
        description="Only blast to buyers at this tier or better (1=A only, 3=all tiers)",
    )
    dry_run:     bool = Field(
        default=False,
        description="Build message and select buyers but skip Twilio call.",
    )


class RunResponse(BaseModel):
    deal_id:             int
    buyers_matched:      int
    buyers_blasted:      int
    skipped_dnc:         int
    skipped_no_phone:    int
    skipped_quiet_hours: bool
    send_errors:         int
    dry_run:             bool
    message_preview:     str
    buyer_results:       dict   # buyer_id → twilio_sid or skip reason


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetch_deal_with_lead(db, deal_id: int) -> dict | None:
    """
    Join deals + leads to get all fields needed for the blast message
    and buyer matching.
    """
    row = db.execute(
        text("""
            SELECT
                d.id              AS deal_id,
                d.arv_mid,
                d.arv_confidence,
                d.repair_estimate,
                d.offer_amount,
                d.assignment_fee,
                d.status          AS deal_status,
                d.psa_signed_at,
                l.id              AS lead_id,
                l.address,
                l.city,
                l.state,
                l.zip
            FROM  deals d
            JOIN  leads l ON l.id = d.lead_id
            WHERE d.id = :deal_id
        """),
        {"deal_id": deal_id},
    ).fetchone()
    return dict(row._mapping) if row else None


def _find_matching_buyers(db, deal: dict, max_buyers: int, max_tier: int) -> list[dict]:
    """
    Segment buyers by:
      - buy_box_min <= offer_amount <= buy_box_max  (or no buy box set)
      - zip IN areas array  (or no area filter set)
      - strategy IN ('wholesale', 'any')  (or null)
      - tier <= max_tier
    Sort: A-tier first, then most recently active.
    """
    rows = db.execute(
        text("""
            SELECT id, name, phone, email, buy_box_min, buy_box_max,
                   areas, strategy, tier, last_active_at
            FROM   buyers
            WHERE  tier <= :max_tier
              AND  (strategy IS NULL OR strategy IN ('wholesale', 'any'))
              AND  (
                       buy_box_min IS NULL
                    OR buy_box_min <= :offer
                   )
              AND  (
                       buy_box_max IS NULL
                    OR buy_box_max >= :offer
                   )
              AND  (
                       areas IS NULL
                    OR array_length(areas, 1) IS NULL
                    OR :zip = ANY(areas)
                   )
              AND  phone IS NOT NULL
            ORDER BY tier ASC, last_active_at DESC NULLS LAST
            LIMIT  :lim
        """),
        {
            "max_tier": max_tier,
            "offer":    deal["offer_amount"] or 0,
            "zip":      deal["zip"] or "",
            "lim":      max_buyers,
        },
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def _update_buyer_activity(db, buyer_ids: list[int]) -> None:
    if buyer_ids:
        db.execute(
            text(
                "UPDATE buyers SET last_active_at = NOW() WHERE id = ANY(:ids)"
            ),
            {"ids": buyer_ids},
        )


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


@app.post("/run", response_model=RunResponse)
def run(req: RunRequest, _: str = Depends(_require_key)):
    # ── 1. TCPA quiet hours ────────────────────────────────────────────────
    if not req.dry_run and not is_within_calling_hours():
        return RunResponse(
            deal_id=req.deal_id, buyers_matched=0, buyers_blasted=0,
            skipped_dnc=0, skipped_no_phone=0, skipped_quiet_hours=True,
            send_errors=0, dry_run=False, message_preview="",
            buyer_results={},
        )

    # ── 2. Load deal + lead ────────────────────────────────────────────────
    with session_ctx() as db:
        deal = _fetch_deal_with_lead(db, req.deal_id)

    if not deal:
        raise HTTPException(status_code=404, detail=f"Deal {req.deal_id} not found")

    if not deal.get("psa_signed_at"):
        raise HTTPException(
            status_code=422,
            detail=f"Deal {req.deal_id} has no psa_signed_at — PSA must be signed before blasting",
        )

    # Require minimum deal data to build a coherent blast message
    missing = [f for f in ("arv_mid", "offer_amount", "assignment_fee") if not deal.get(f)]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Deal {req.deal_id} is missing required fields: {missing}",
        )

    # ── 3. Build deal blast message ────────────────────────────────────────
    message = build_deal_blast(
        zip_code        = deal.get("zip", ""),
        city            = deal.get("city", "Indianapolis"),
        arv_mid         = deal["arv_mid"],
        repair_estimate = deal.get("repair_estimate") or 0,
        offer_amount    = deal["offer_amount"],
        assignment_fee  = deal["assignment_fee"],
        arv_confidence  = deal.get("arv_confidence") or "medium",
    )

    # ── 4. Find matching buyers ────────────────────────────────────────────
    with session_ctx() as db:
        buyers = _find_matching_buyers(db, deal, req.max_buyers, req.max_tier)

    if not buyers:
        return RunResponse(
            deal_id=req.deal_id, buyers_matched=0, buyers_blasted=0,
            skipped_dnc=0, skipped_no_phone=0, skipped_quiet_hours=False,
            send_errors=0, dry_run=req.dry_run, message_preview=message,
            buyer_results={},
        )

    log.info("deal_id=%s matched %d buyers", req.deal_id, len(buyers))

    # ── 5. DNC scrub buyer phones ──────────────────────────────────────────
    phones_by_buyer = {b["id"]: b["phone"] for b in buyers if b.get("phone")}
    skipped_no_phone = len(buyers) - len(phones_by_buyer)

    clean_phones: set[str] = set()
    if phones_by_buyer and not req.dry_run:
        clean_phones = scrub_phones(list(phones_by_buyer.values()))
        log.info(
            "Buyer DNC scrub: %d in → %d clean",
            len(phones_by_buyer), len(clean_phones),
        )
    elif req.dry_run:
        clean_phones = set(phones_by_buyer.values())

    # ── 6. Blast ───────────────────────────────────────────────────────────
    buyers_blasted  = 0
    skipped_dnc     = 0
    send_errors     = 0
    buyer_results: dict = {}
    blasted_ids:   list[int] = []

    for buyer in buyers:
        bid   = buyer["id"]
        phone = phones_by_buyer.get(bid)

        if not phone:
            buyer_results[bid] = "no_phone"
            continue

        if phone not in clean_phones:
            skipped_dnc += 1
            buyer_results[bid] = "skipped_dnc"
            continue

        sid = "dry_run"
        if not req.dry_run:
            try:
                sid = send_sms(phone, message)
            except Exception as exc:
                log.error("Twilio error buyer_id=%s: %s", bid, exc)
                send_errors += 1
                buyer_results[bid] = f"send_error: {exc}"
                continue

        buyers_blasted += 1
        blasted_ids.append(bid)
        buyer_results[bid] = sid

    # ── 7. Post-blast DB updates ───────────────────────────────────────────
    if blasted_ids and not req.dry_run:
        with session_ctx() as db:
            _update_buyer_activity(db, blasted_ids)

    log_agent_event(
        source_agent=AGENT_NAME,
        target_agent="twilio" if not req.dry_run else "dry_run",
        payload={"deal_id": req.deal_id, "buyers_matched": len(buyers)},
        response={
            "buyers_blasted": buyers_blasted,
            "skipped_dnc":    skipped_dnc,
            "send_errors":    send_errors,
        },
        status_code=200,
    )

    return RunResponse(
        deal_id             = req.deal_id,
        buyers_matched      = len(buyers),
        buyers_blasted      = buyers_blasted,
        skipped_dnc         = skipped_dnc,
        skipped_no_phone    = skipped_no_phone,
        skipped_quiet_hours = False,
        send_errors         = send_errors,
        dry_run             = req.dry_run,
        message_preview     = message,
        buyer_results       = buyer_results,
    )
