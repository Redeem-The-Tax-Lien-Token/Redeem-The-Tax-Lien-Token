"""
Intake Agent — FastAPI app.

Endpoints:
  GET  /              health check
  POST /run           manual classification (X-Internal-Key required)
  GET  /version       git commit hash
  POST /sms/inbound   Twilio webhook — classify inbound SMS replies

Flow for /sms/inbound:
  1. Return TwiML ACK immediately (Twilio requires < 5 s)
  2. Background task: log → lookup lead → classify → update status → write scores
"""

import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy import text
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

# ── Import path: monorepo root OR build_repl.sh copy ──────────────────────────
_here      = Path(__file__).parent
_repo_root = _here.parent.parent
for _p in [str(_here), str(_repo_root)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.db import log_agent_event, session_ctx  # noqa: E402
from agents import classify_reply                     # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────

AGENT_NAME       = "intake"
INTERNAL_API_KEY = os.environ["INTERNAL_API_KEY"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]

# Set TWILIO_VALIDATE_SIGNATURE=false only in local dev/testing
_VALIDATE_SIG = os.environ.get("TWILIO_VALIDATE_SIGNATURE", "true").lower() == "true"

log = logging.getLogger(AGENT_NAME)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Intake Agent", version="1.0.0")
_key_header = APIKeyHeader(name="X-Internal-Key", auto_error=False)

# ── Valid lead status transitions (state-machine guard) ───────────────────────
# Mirrors the CHECK constraint in migrate.sql.  DNC, closed, dead are terminal.
ALLOWED_TRANSITIONS: dict[str, frozenset] = {
    "new":            frozenset({"traced", "contacted", "hot", "warm", "cold", "dnc"}),
    "traced":         frozenset({"contacted", "hot", "warm", "cold", "dnc"}),
    "contacted":      frozenset({"hot", "warm", "cold", "dnc"}),
    "hot":            frozenset({"under_contract", "warm", "cold", "dead", "dnc"}),
    "warm":           frozenset({"hot", "cold", "dead", "dnc"}),
    "cold":           frozenset({"warm", "dead", "dnc"}),
    "dnc":            frozenset(),
    "under_contract": frozenset({"closed", "dead"}),
    "closed":         frozenset(),
    "dead":           frozenset(),
}

# Classification → lead status mapping. OTHER leaves status unchanged.
_CLASS_TO_STATUS = {"HOT": "hot", "WARM": "warm", "COLD": "cold", "DNC": "dnc"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _digits_only(phone: str) -> str:
    """Strip everything except digits for fuzzy phone matching."""
    return re.sub(r"\D", "", phone)


def _normalize_e164(phone: str) -> str:
    """Best-effort E.164 normalization for 10-digit US numbers."""
    digits = _digits_only(phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return phone  # return as-is if format is unexpected


def _lookup_lead(db, phone: str) -> Optional[dict]:
    """Find lead by phone, tolerating format differences."""
    digits = _digits_only(phone)
    row = db.execute(
        text(
            "SELECT * FROM leads "
            "WHERE regexp_replace(phone, '[^0-9]', '', 'g') = :digits "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"digits": digits},
    ).fetchone()
    return dict(row._mapping) if row else None


def _create_unknown_lead(db, phone: str) -> int:
    """
    Insert a minimal lead row when an inbound SMS has no matching record.
    Returns the new lead id.
    """
    row = db.execute(
        text(
            "INSERT INTO leads (address, phone, list_source, status) "
            "VALUES ('UNKNOWN', :phone, 'inbound_reply', 'contacted') "
            "RETURNING id"
        ),
        {"phone": _normalize_e164(phone)},
    ).fetchone()
    return row[0]


async def _validate_twilio_signature(request: Request) -> bool:
    if not _VALIDATE_SIG:
        return True
    validator   = RequestValidator(TWILIO_AUTH_TOKEN)
    form_data   = await request.form()
    signature   = request.headers.get("X-Twilio-Signature", "")
    url         = str(request.url)
    return validator.validate(url, dict(form_data), signature)


# ── Auth dependency ────────────────────────────────────────────────────────────

async def _require_key(key: str = Security(_key_header)) -> str:
    if key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Internal-Key")
    return key


# ── Background task ───────────────────────────────────────────────────────────

def _classify_and_route(from_number: str, body: str) -> None:
    """
    Called as a BackgroundTask so Twilio gets its 200 response in < 5 s.

    Steps:
      1. Log inbound message to outreach_log
      2. Look up (or create) lead by phone
      3. Classify with Claude
      4. Write classification to lead_scores
      5. Update lead status if the transition is valid
    """
    try:
        with session_ctx() as db:
            # 1. Look up / create lead
            lead = _lookup_lead(db, from_number)
            if lead:
                lead_id = lead["id"]
            else:
                lead_id = _create_unknown_lead(db, from_number)
                lead    = {"id": lead_id, "status": "contacted"}
                log.info("Created unknown lead id=%s for %s", lead_id, from_number)

            # 2. Log the inbound message
            db.execute(
                text("""
                    INSERT INTO outreach_log
                        (lead_id, message, channel, direction, status, to_number)
                    VALUES
                        (:lead_id, :message, 'sms', 'inbound', 'received', :to_number)
                """),
                {
                    "lead_id":   lead_id,
                    "message":   body,
                    "to_number": _normalize_e164(from_number),
                },
            )

        # 3. Classify (outside the DB session — Claude call can be slow)
        result = classify_reply(body)
        classification = result["classification"]
        log.info("lead_id=%s classified as %s", lead_id, classification)

        with session_ctx() as db:
            # 4. Write classification to lead_scores
            db.execute(
                text("""
                    INSERT INTO lead_scores
                        (lead_id, classification, raw_reply, reason)
                    VALUES
                        (:lead_id, :classification, :raw_reply, :reason)
                """),
                {
                    "lead_id":        lead_id,
                    "classification": classification,
                    "raw_reply":      body,
                    "reason":         result.get("reason", ""),
                },
            )
            # NOTE: the sync_dnc_to_lead DB trigger auto-flips status to 'dnc'
            # when classification = 'DNC', so we don't need a separate UPDATE
            # for that case. For all other valid transitions we do it explicitly.

            # 5. Update lead status if classification maps to a status
            new_status = _CLASS_TO_STATUS.get(classification)
            current    = lead.get("status", "new")

            if new_status and new_status != current:
                if new_status in ALLOWED_TRANSITIONS.get(current, frozenset()):
                    db.execute(
                        text("UPDATE leads SET status = :status WHERE id = :id"),
                        {"status": new_status, "id": lead_id},
                    )
                    log.info(
                        "lead_id=%s status %s → %s", lead_id, current, new_status
                    )
                else:
                    log.warning(
                        "lead_id=%s invalid transition %s → %s (skipped)",
                        lead_id, current, new_status,
                    )

        log_agent_event(
            source_agent="twilio",
            target_agent=AGENT_NAME,
            payload={"from": from_number, "body": body[:200]},
            response=result,
            status_code=200,
        )

    except Exception:
        log.exception("classify_and_route failed for %s", from_number)


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


class RunRequest(BaseModel):
    from_number: str
    body:        str
    lead_id:     Optional[int] = None


@app.post("/run")
def run(req: RunRequest, _: str = Depends(_require_key)):
    """
    Manual classification endpoint for testing or orchestration.
    Runs synchronously and returns the classification result.
    """
    try:
        result = classify_reply(req.body)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Classification error: {exc}")

    classification = result["classification"]
    new_status     = _CLASS_TO_STATUS.get(classification)
    db_warning     = None

    if req.lead_id and new_status:
        try:
            with session_ctx() as db:
                lead = db.execute(
                    text("SELECT status FROM leads WHERE id = :id"),
                    {"id": req.lead_id},
                ).fetchone()

                if not lead:
                    db_warning = f"lead_id={req.lead_id} not found"
                else:
                    current = lead[0]
                    db.execute(
                        text("""
                            INSERT INTO lead_scores
                                (lead_id, classification, raw_reply, reason)
                            VALUES
                                (:lead_id, :cls, :reply, :reason)
                        """),
                        {
                            "lead_id": req.lead_id,
                            "cls":     classification,
                            "reply":   req.body,
                            "reason":  result.get("reason", ""),
                        },
                    )
                    if new_status in ALLOWED_TRANSITIONS.get(current, frozenset()):
                        db.execute(
                            text("UPDATE leads SET status = :s WHERE id = :id"),
                            {"s": new_status, "id": req.lead_id},
                        )
        except Exception as exc:
            db_warning = f"DB write failed: {exc}"

    return {**result, "db_warning": db_warning}


@app.post("/sms/inbound")
async def sms_inbound(
    request:          Request,
    background_tasks: BackgroundTasks,
    # Form fields injected by Twilio
    From: str = None,
    Body: str = None,
):
    """
    Twilio webhook. Must respond with TwiML in < 5 s.
    Heavy work (Claude call, DB writes) runs in the background.
    """
    # Re-parse form if FastAPI didn't inject the Form params
    # (happens when called with raw form body without explicit Form(...) sig)
    if From is None or Body is None:
        form = await request.form()
        From = form.get("From", "")
        Body = form.get("Body", "")

    if not From:
        raise HTTPException(status_code=400, detail="Missing 'From' field")

    # Validate Twilio signature (TWILIO_VALIDATE_SIGNATURE=false to skip in dev)
    if not await _validate_twilio_signature(request):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    background_tasks.add_task(_classify_and_route, From, Body or "")

    resp = MessagingResponse()
    resp.message("Thank you! A team member will follow up shortly.")
    return Response(content=str(resp), media_type="application/xml")
