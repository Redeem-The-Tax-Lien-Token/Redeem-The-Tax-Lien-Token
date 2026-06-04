"""
List Puller Agent — FastAPI app.

Queries ATTOM for distressed / motivated-seller property lists by zip code,
deduplicates against the existing leads table, and batch-inserts new records.

Endpoints:
  GET  /      health check
  POST /run   pull list (X-Internal-Key required)
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
from attom_utils import pull_leads                   # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────

AGENT_NAME       = "list-puller"
INTERNAL_API_KEY = os.environ["INTERNAL_API_KEY"]

VALID_MOTIVATIONS = frozenset({
    "pre_foreclosure",
    "high_equity",
    "tax_delinquent",
    "vacant",
})

log = logging.getLogger(AGENT_NAME)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="List Puller Agent", version="1.0.0")
_key_header = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def _require_key(key: str = Security(_key_header)) -> str:
    if key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Internal-Key")
    return key


# ── Request / Response models ─────────────────────────────────────────────────

class RunRequest(BaseModel):
    zip_codes:        list[str] = Field(..., min_length=1)
    motivation_types: list[str] = Field(
        default=["pre_foreclosure", "high_equity"],
        description="One or more of: pre_foreclosure, high_equity, tax_delinquent, vacant",
    )
    property_type:    str   = "SFR"
    min_equity_pct:   float = Field(default=30.0, ge=0, le=100)
    min_years_owned:  int   = Field(default=2,    ge=0)
    max_per_zip:      int   = Field(default=50,   ge=1, le=200)


class RunResponse(BaseModel):
    new_leads:           int
    skipped_duplicates:  int
    attom_errors:        int
    zips_processed:      int
    motivation_summary:  dict[str, int]
    sample_addresses:    list[str]


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
    # Validate motivation types upfront
    bad = [m for m in req.motivation_types if m not in VALID_MOTIVATIONS]
    if bad:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown motivation type(s): {bad}. "
                   f"Valid: {sorted(VALID_MOTIVATIONS)}",
        )

    new_leads          = 0
    skipped_duplicates = 0
    attom_errors       = 0
    motivation_summary: dict[str, int] = {m: 0 for m in req.motivation_types}
    sample_addresses:   list[str]      = []

    for zip_code in req.zip_codes:
        for motivation in req.motivation_types:
            try:
                raw_leads = pull_leads(
                    zip_code        = zip_code,
                    motivation      = motivation,
                    property_type   = req.property_type,
                    min_equity_pct  = req.min_equity_pct,
                    min_years_owned = req.min_years_owned,
                    max_results     = req.max_per_zip,
                )
            except Exception as exc:
                log.error(
                    "ATTOM error zip=%s motivation=%s: %s", zip_code, motivation, exc
                )
                attom_errors += 1
                continue

            if not raw_leads:
                log.info("No results: zip=%s motivation=%s", zip_code, motivation)
                continue

            # Collect attom_ids from this batch so we can check existence in bulk
            batch_ids = [r["attom_id"] for r in raw_leads if r.get("attom_id")]

            try:
                with session_ctx() as db:
                    # Find which attom_ids already exist
                    existing = set()
                    if batch_ids:
                        rows = db.execute(
                            text(
                                "SELECT attom_id FROM leads "
                                "WHERE attom_id = ANY(:ids)"
                            ),
                            {"ids": batch_ids},
                        ).fetchall()
                        existing = {r[0] for r in rows}

                    to_insert = [
                        r for r in raw_leads
                        if r.get("attom_id") not in existing
                    ]
                    skipped_duplicates += len(raw_leads) - len(to_insert)

                    for lead in to_insert:
                        db.execute(
                            text("""
                                INSERT INTO leads
                                    (address, city, state, zip, owner_name,
                                     attom_id, motivation_type, equity_pct,
                                     list_source, status)
                                VALUES
                                    (:address, :city, :state, :zip, :owner_name,
                                     :attom_id, :motivation_type, :equity_pct,
                                     :list_source, :status)
                                ON CONFLICT (attom_id) DO NOTHING
                            """),
                            lead,
                        )
                        new_leads += 1
                        motivation_summary[motivation] += 1
                        if len(sample_addresses) < 5:
                            sample_addresses.append(
                                f"{lead['address']}, {lead['city']} {lead['zip']}"
                            )

            except Exception as exc:
                log.error(
                    "DB error zip=%s motivation=%s: %s", zip_code, motivation, exc
                )
                attom_errors += 1

    log_agent_event(
        source_agent=AGENT_NAME,
        target_agent="database",
        payload={
            "zip_codes":        req.zip_codes,
            "motivation_types": req.motivation_types,
        },
        response={
            "new_leads":          new_leads,
            "skipped_duplicates": skipped_duplicates,
            "attom_errors":       attom_errors,
        },
        status_code=200 if attom_errors == 0 else 207,
    )

    return RunResponse(
        new_leads          = new_leads,
        skipped_duplicates = skipped_duplicates,
        attom_errors       = attom_errors,
        zips_processed     = len(req.zip_codes),
        motivation_summary = motivation_summary,
        sample_addresses   = sample_addresses,
    )
