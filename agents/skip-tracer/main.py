"""
Skip Tracer Agent — FastAPI app.

Pulls untraced leads from the DB (or accepts explicit lead_ids),
calls Tracerfy instant trace, batch DNC-scrubs all returned phones,
then updates each lead with the best clean phone + email.

Endpoints:
  GET  /      health check
  POST /run   run skip trace (X-Internal-Key required)
  GET  /version
"""

import logging
import os
import subprocess
import sys
import time
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
from tracerfy_utils import (                         # noqa: E402
    best_phone,
    extract_email,
    extract_phones,
    has_litigator_flag,
    is_deceased,
    scrub_phones,
    trace_single,
)

# ── Config ────────────────────────────────────────────────────────────────────

AGENT_NAME       = "skip-tracer"
INTERNAL_API_KEY = os.environ["INTERNAL_API_KEY"]

# Courtesy delay between Tracerfy instant-trace calls (500 RPM limit)
_TRACE_DELAY = 0.15  # seconds

log = logging.getLogger(AGENT_NAME)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Skip Tracer Agent", version="1.0.0")
_key_header = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def _require_key(key: str = Security(_key_header)) -> str:
    if key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Internal-Key")
    return key


# ── Models ────────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    lead_ids:   Optional[list[int]] = Field(
        default=None,
        description="Trace specific leads. If omitted, pulls untraced 'new' leads.",
    )
    max_leads:  int = Field(default=50, ge=1, le=500)
    batch_size: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Max leads per DNC scrub call (cost: 1 credit/phone).",
    )


class RunResponse(BaseModel):
    processed:          int
    traced_ok:          int
    deceased_skipped:   int
    litigator_skipped:  int
    no_phone_found:     int
    all_dnc:            int
    trace_errors:       int
    leads_updated:      dict  # lead_id → outcome


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetch_untraced_leads(db, max_leads: int) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT id, address, city, state, zip
            FROM   leads
            WHERE  status IN ('new', 'traced')
              AND  skip_traced_at IS NULL
            ORDER BY created_at
            LIMIT  :lim
        """),
        {"lim": max_leads},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def _fetch_leads_by_ids(db, lead_ids: list[int]) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT id, address, city, state, zip
            FROM   leads
            WHERE  id = ANY(:ids)
        """),
        {"ids": lead_ids},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


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
def run(req: RunRequest, _: str = Depends(_require_key)):  # noqa: C901
    # ── 1. Fetch leads to trace ────────────────────────────────────────────
    with session_ctx() as db:
        if req.lead_ids:
            leads = _fetch_leads_by_ids(db, req.lead_ids)
        else:
            leads = _fetch_untraced_leads(db, req.max_leads)

    if not leads:
        return RunResponse(
            processed=0, traced_ok=0, deceased_skipped=0,
            litigator_skipped=0, no_phone_found=0, all_dnc=0,
            trace_errors=0, leads_updated={},
        )

    log.info("Skip-tracing %d leads", len(leads))

    # ── 2. Run instant trace for every lead ────────────────────────────────
    # Collect results keyed by lead_id; also gather all phone numbers for
    # one batch DNC scrub rather than one scrub call per lead.
    trace_results: dict[int, dict]  = {}   # lead_id → trace response
    all_phones:    list[str]        = []   # flat list for batch DNC scrub
    phone_to_lead: dict[str, int]   = {}   # phone → lead_id (for reverse lookup)
    trace_errors   = 0

    for lead in leads:
        try:
            result = trace_single(
                lead["address"],
                lead.get("city", "Indianapolis"),
                lead.get("state", "IN"),
            )
            phones = extract_phones(result)

            # Skip entirely if owner is deceased
            if is_deceased(result):
                trace_results[lead["id"]] = {"_outcome": "deceased"}
                continue

            trace_results[lead["id"]] = result

            for p in phones:
                all_phones.append(p)
                phone_to_lead.setdefault(p, lead["id"])  # first-seen mapping

        except Exception as exc:
            log.warning("Trace error lead_id=%s: %s", lead["id"], exc)
            trace_results[lead["id"]] = {"_outcome": "error"}
            trace_errors += 1

        time.sleep(_TRACE_DELAY)

    # ── 3. Batch DNC scrub (fail-closed) ──────────────────────────────────
    clean_phones: set[str] = set()
    if all_phones:
        clean_phones = scrub_phones(list(set(all_phones)))
        log.info(
            "DNC scrub: %d phones in → %d clean", len(all_phones), len(clean_phones)
        )
        if not clean_phones and all_phones:
            log.warning("DNC scrub returned empty (fail-closed) — no phones cleared")

    # ── 4. Update each lead in DB ──────────────────────────────────────────
    traced_ok          = 0
    deceased_skipped   = 0
    litigator_skipped  = 0
    no_phone_found     = 0
    all_dnc_count      = 0
    leads_updated: dict = {}

    with session_ctx() as db:
        for lead in leads:
            lid    = lead["id"]
            result = trace_results.get(lid)

            if result is None:
                continue

            # Deceased — mark lead dead and move on
            if result.get("_outcome") == "deceased":
                db.execute(
                    text("UPDATE leads SET skip_traced_at = NOW(), status = 'dead' WHERE id = :id"),
                    {"id": lid},
                )
                deceased_skipped += 1
                leads_updated[lid] = "deceased→dead"
                continue

            if result.get("_outcome") == "error":
                leads_updated[lid] = "trace_error"
                continue

            phones_ranked = extract_phones(result)

            # Check for litigator flag on the owner
            if has_litigator_flag(result):
                db.execute(
                    text("""
                        UPDATE leads
                        SET    skip_traced_at = NOW(),
                               dnc_checked    = TRUE,
                               status         = 'dnc'
                        WHERE  id = :id
                    """),
                    {"id": lid},
                )
                litigator_skipped += 1
                leads_updated[lid] = "litigator→dnc"
                continue

            phone = best_phone(phones_ranked, clean_phones)
            email = extract_email(result)

            if not phone and phones_ranked:
                # Had phones but all were DNC after scrub
                db.execute(
                    text("""
                        UPDATE leads
                        SET    skip_traced_at = NOW(),
                               dnc_checked    = TRUE,
                               status         = 'dnc'
                        WHERE  id = :id
                    """),
                    {"id": lid},
                )
                all_dnc_count += 1
                leads_updated[lid] = "all_phones_dnc→dnc"
                continue

            if not phone:
                # Trace returned no phones at all
                db.execute(
                    text("""
                        UPDATE leads
                        SET    skip_traced_at = NOW(),
                               dnc_checked    = TRUE
                        WHERE  id = :id
                    """),
                    {"id": lid},
                )
                no_phone_found += 1
                leads_updated[lid] = "no_phone_found"
                continue

            # Happy path — update phone, email, and advance to 'traced'
            db.execute(
                text("""
                    UPDATE leads
                    SET    phone          = :phone,
                           email          = :email,
                           skip_traced_at = NOW(),
                           dnc_checked    = TRUE,
                           status         = 'traced'
                    WHERE  id = :id
                      AND  status NOT IN ('dnc', 'closed', 'dead')
                """),
                {
                    "phone": f"+1{phone}",
                    "email": email,
                    "id":    lid,
                },
            )
            traced_ok += 1
            leads_updated[lid] = f"traced→+1{phone}"

    log_agent_event(
        source_agent=AGENT_NAME,
        target_agent="database",
        payload={"lead_count": len(leads)},
        response={
            "traced_ok":         traced_ok,
            "deceased_skipped":  deceased_skipped,
            "all_dnc":           all_dnc_count,
            "trace_errors":      trace_errors,
        },
        status_code=200,
    )

    return RunResponse(
        processed         = len(leads),
        traced_ok         = traced_ok,
        deceased_skipped  = deceased_skipped,
        litigator_skipped = litigator_skipped,
        no_phone_found    = no_phone_found,
        all_dnc           = all_dnc_count,
        trace_errors      = trace_errors,
        leads_updated     = leads_updated,
    )
