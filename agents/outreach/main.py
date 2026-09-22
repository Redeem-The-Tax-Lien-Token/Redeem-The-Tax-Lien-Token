"""
Outreach Agent — FastAPI app.

Sends personalized, HB-1068-compliant SMS to traced leads via Twilio A2P 10DLC
Messaging Service. Enforces TCPA quiet hours, re-scrubs all phones for DNC before
each batch, and logs every send (with disclosure_version) to outreach_log.

Endpoints:
  GET  /      health check
  POST /run   send outreach batch (X-Internal-Key required)
  GET  /version

⚠️ COMPLIANCE: Every outbound seller SMS is linted before send. A failed lint
blocks the send and logs COMPLIANCE_BLOCK. (§2.1, §9)
"""

import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager
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

from shared.db import log_agent_event, session_ctx                  # noqa: E402
from shared.compliance.disclosures import build_seller_sms          # noqa: E402
from shared.compliance.linter import check_outbound                 # noqa: E402
from tracerfy_utils import scrub_phones                             # noqa: E402
from twilio_utils import (                                          # noqa: E402
    build_message,
    is_within_calling_hours,
    send_sms,
    twilio_startup_check,
    TWILIO_MESSAGING_SERVICE_SID,
)

# ── Config ────────────────────────────────────────────────────────────────────

AGENT_NAME       = "outreach"
INTERNAL_API_KEY = os.environ["INTERNAL_API_KEY"]
SYSTEM_MODE      = os.environ.get("SYSTEM_MODE", "live")  # "dry_run" or "live"

log = logging.getLogger(AGENT_NAME)
logging.basicConfig(level=logging.INFO)


# ── Lifespan: startup self-check ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    twilio_startup_check()   # raises RuntimeError → fail closed if 401/bad SID
    yield


app = FastAPI(title="Outreach Agent", version="2.0.0", lifespan=lifespan)
_key_header = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def _require_key(key: str = Security(_key_header)) -> str:
    if key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Internal-Key")
    return key


# ── Models ────────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    touch_number: int = Field(
        default=1, ge=1, le=3,
        description="Which cadence touch to send (1=initial, 2=day-3 follow-up, 3=day-7 final)",
    )
    lead_ids:     Optional[list[int]] = Field(
        default=None,
        description="Send to specific lead IDs. If null, auto-pulls based on touch logic.",
    )
    max_leads:    int  = Field(default=50, ge=1, le=500)
    dry_run:      bool = Field(
        default=False,
        description="Log what would be sent without calling Twilio. Safe for testing.",
    )


class RunResponse(BaseModel):
    sent:                int
    skipped_dnc:         int
    skipped_no_phone:    int
    skipped_quiet_hours: bool
    skipped_lint_fail:   int
    send_errors:         int
    dry_run:             bool
    leads_sent:          dict   # lead_id → twilio_sid or skip reason


# ── Lead queries ──────────────────────────────────────────────────────────────

def _pull_touch_1(db, max_leads: int) -> list[dict]:
    """Leads that are traced and have never received an outbound SMS."""
    rows = db.execute(
        text("""
            SELECT l.id, l.phone, l.owner_name, l.address, l.city, l.state, l.zip
            FROM   leads l
            WHERE  l.status = 'traced'
              AND  l.phone IS NOT NULL
              AND  l.dnc_checked = TRUE
              AND  NOT EXISTS (
                       SELECT 1 FROM outreach_log o
                       WHERE  o.lead_id  = l.id
                         AND  o.direction = 'outbound'
                   )
            ORDER BY l.created_at
            LIMIT  :lim
        """),
        {"lim": max_leads},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def _pull_touch_2(db, max_leads: int) -> list[dict]:
    """Leads contacted exactly once, no inbound reply, last outbound ≥ 3 days ago."""
    rows = db.execute(
        text("""
            SELECT l.id, l.phone, l.owner_name, l.address, l.city, l.state, l.zip
            FROM   leads l
            WHERE  l.status = 'contacted'
              AND  l.phone IS NOT NULL
              AND  l.dnc_checked = TRUE
              AND  (
                       SELECT COUNT(*) FROM outreach_log o
                       WHERE o.lead_id = l.id AND o.direction = 'outbound'
                   ) = 1
              AND  (
                       SELECT MAX(sent_at) FROM outreach_log o
                       WHERE o.lead_id = l.id AND o.direction = 'outbound'
                   ) < NOW() - INTERVAL '3 days'
              AND  NOT EXISTS (
                       SELECT 1 FROM outreach_log o
                       WHERE o.lead_id = l.id AND o.direction = 'inbound'
                   )
            ORDER BY l.created_at
            LIMIT  :lim
        """),
        {"lim": max_leads},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def _pull_touch_3(db, max_leads: int) -> list[dict]:
    """Leads contacted exactly twice, no inbound reply, last outbound ≥ 7 days ago."""
    rows = db.execute(
        text("""
            SELECT l.id, l.phone, l.owner_name, l.address, l.city, l.state, l.zip
            FROM   leads l
            WHERE  l.status = 'contacted'
              AND  l.phone IS NOT NULL
              AND  l.dnc_checked = TRUE
              AND  (
                       SELECT COUNT(*) FROM outreach_log o
                       WHERE o.lead_id = l.id AND o.direction = 'outbound'
                   ) = 2
              AND  (
                       SELECT MAX(sent_at) FROM outreach_log o
                       WHERE o.lead_id = l.id AND o.direction = 'outbound'
                   ) < NOW() - INTERVAL '7 days'
              AND  NOT EXISTS (
                       SELECT 1 FROM outreach_log o
                       WHERE o.lead_id = l.id AND o.direction = 'inbound'
                   )
            ORDER BY l.created_at
            LIMIT  :lim
        """),
        {"lim": max_leads},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def _pull_by_ids(db, lead_ids: list[int]) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT id, phone, owner_name, address, city, state, zip
            FROM   leads
            WHERE  id = ANY(:ids)
              AND  phone IS NOT NULL
              AND  dnc_checked = TRUE
              AND  status NOT IN ('dnc', 'dead', 'closed')
        """),
        {"ids": lead_ids},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


_PULL_FNS = {1: _pull_touch_1, 2: _pull_touch_2, 3: _pull_touch_3}


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
    is_dry_run = req.dry_run or (SYSTEM_MODE == "dry_run")

    # ── 1. TCPA quiet-hours check ──────────────────────────────────────────
    if not is_dry_run and not is_within_calling_hours():
        return RunResponse(
            sent=0, skipped_dnc=0, skipped_no_phone=0,
            skipped_quiet_hours=True, skipped_lint_fail=0,
            send_errors=0, dry_run=is_dry_run, leads_sent={},
        )

    # ── 2. Pull leads ──────────────────────────────────────────────────────
    with session_ctx() as db:
        if req.lead_ids:
            leads = _pull_by_ids(db, req.lead_ids)
        else:
            pull_fn = _PULL_FNS[req.touch_number]
            leads   = pull_fn(db, req.max_leads)

    if not leads:
        return RunResponse(
            sent=0, skipped_dnc=0, skipped_no_phone=0,
            skipped_quiet_hours=False, skipped_lint_fail=0,
            send_errors=0, dry_run=is_dry_run, leads_sent={},
        )

    # ── 3. Collect phones and re-scrub for DNC (fail-closed) ─────────────
    phones_by_lead = {
        lead["id"]: lead["phone"]
        for lead in leads
        if lead.get("phone")
    }
    skipped_no_phone = len(leads) - len(phones_by_lead)

    clean_phones: set[str] = set()
    if phones_by_lead and not is_dry_run:
        clean_phones = scrub_phones(list(phones_by_lead.values()))
        log.info(
            "DNC re-scrub: %d phones in → %d clean",
            len(phones_by_lead), len(clean_phones),
        )
        if not clean_phones:
            log.warning("DNC scrub returned empty (fail-closed) — no sends")
    elif is_dry_run:
        clean_phones = set(phones_by_lead.values())

    # ── 4. Send ────────────────────────────────────────────────────────────
    sent              = 0
    skipped_dnc       = 0
    skipped_lint_fail = 0
    send_errors       = 0
    leads_sent: dict  = {}

    with session_ctx() as db:
        for lead in leads:
            lid   = lead["id"]
            phone = phones_by_lead.get(lid)

            if not phone:
                continue

            if phone not in clean_phones:
                db.execute(
                    text(
                        "UPDATE leads SET status = 'dnc' "
                        "WHERE id = :id AND status NOT IN ('dnc','closed','dead')"
                    ),
                    {"id": lid},
                )
                skipped_dnc += 1
                leads_sent[lid] = "skipped_dnc"
                continue

            # Build body → inject disclosure + opt-out via build_seller_sms
            body = build_message(
                touch_number = req.touch_number,
                owner_name   = lead.get("owner_name"),
                address      = lead.get("address", "your property"),
                city         = lead.get("city", "Indianapolis"),
            )
            try:
                full_message, disclosure_version = build_seller_sms(body)
            except RuntimeError as exc:
                log.error("COMPLIANCE_BLOCK: disclosure load failed lead_id=%s: %s", lid, exc)
                skipped_lint_fail += 1
                leads_sent[lid] = f"compliance_block: {exc}"
                continue

            # Run compliance linter before any send
            lint = check_outbound(full_message, disclosure_version)
            if not lint.ok:
                log.error(
                    "COMPLIANCE_BLOCK lead_id=%s segments=%d reason=%s",
                    lid, lint.segment_count, lint.reason,
                )
                skipped_lint_fail += 1
                leads_sent[lid] = f"compliance_block: {lint.reason}"
                continue

            sid = "dry_run"
            if not is_dry_run:
                try:
                    sid = send_sms(phone, full_message)
                except Exception as exc:
                    log.error("Twilio error lead_id=%s: %s", lid, exc)
                    send_errors += 1
                    leads_sent[lid] = f"send_error: {exc}"
                    continue

            # Log to outreach_log with disclosure_version
            db.execute(
                text("""
                    INSERT INTO outreach_log
                        (lead_id, message, channel, direction, status,
                         twilio_sid, from_number, to_number, disclosure_version)
                    VALUES
                        (:lead_id, :message, 'sms', 'outbound',
                         :status, :twilio_sid, :from_num, :to_num, :disc_ver)
                """),
                {
                    "lead_id":          lid,
                    "message":          full_message,
                    "status":           "sent" if not is_dry_run else "dry_run",
                    "twilio_sid":       sid if not is_dry_run else None,
                    "from_num":         TWILIO_MESSAGING_SERVICE_SID,
                    "to_num":           phone,
                    "disc_ver":         disclosure_version,
                },
            )

            if lead.get("status") == "traced":
                db.execute(
                    text(
                        "UPDATE leads SET status = 'contacted' "
                        "WHERE id = :id AND status = 'traced'"
                    ),
                    {"id": lid},
                )

            sent += 1
            leads_sent[lid] = sid

    log_agent_event(
        source_agent=AGENT_NAME,
        target_agent="twilio" if not is_dry_run else "dry_run",
        payload={"touch_number": req.touch_number, "lead_count": len(leads)},
        response={
            "sent": sent, "skipped_dnc": skipped_dnc,
            "skipped_lint_fail": skipped_lint_fail, "errors": send_errors,
        },
        status_code=200,
    )

    return RunResponse(
        sent                 = sent,
        skipped_dnc          = skipped_dnc,
        skipped_no_phone     = skipped_no_phone,
        skipped_quiet_hours  = False,
        skipped_lint_fail    = skipped_lint_fail,
        send_errors          = send_errors,
        dry_run              = is_dry_run,
        leads_sent           = leads_sent,
    )
