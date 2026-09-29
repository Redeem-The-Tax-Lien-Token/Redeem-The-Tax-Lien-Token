"""
Agent 10 — Voice (FastAPI service).

Twilio ConversationRelay webhook handlers.

Endpoints:
  POST /voice/inbound          — TwiML: handle incoming seller call
  POST /voice/status           — Twilio call status callback
  POST /voice/callback-request — schedule an outbound callback to a seller
                                  (operator-requested or seller-requested; NEVER
                                  autonomous cold AI voice calls)
  GET  /                       — health check

Contract (§7, Agent 10 + §2.1):
  - INBOUND and seller-requested CALLBACKS only. No cold AI-voice outbound calls.
  - Every call opens with the code-rendered automated-agent + HB 1068 disclosure
    BEFORE the LLM says anything substantive. The opening is produced by
    opening_script.build_opening() — never modified by the LLM.
  - Transfer to operator on "transfer" keyword — sends TwiML <Dial>.
  - TCPA call recording consent: call is announced as recorded before recording starts.
  - Calls are logged to outreach_log with disclosure_version.
  - SYSTEM_MODE=dry_run → all outbound callbacks go to log, not Twilio.

Twilio ConversationRelay overview:
  1. Inbound call hits /voice/inbound → returns TwiML <Connect><ConversationRelay>.
  2. ConversationRelay manages audio ↔ LLM WebSocket bridge at /voice/relay.
  3. Call status updates arrive at /voice/status.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from shared.db import session_ctx, log_agent_event

from .opening_script import build_opening, build_system_prompt, invalidate_cache

import yaml as _yaml

log = logging.getLogger(__name__)

_SYSTEM_MODE      = os.environ.get("SYSTEM_MODE", "live")
_OPERATOR_PHONE   = os.environ.get("OPERATOR_PHONE", "")
_TWILIO_SID       = os.environ.get("TWILIO_ACCOUNT_SID", "")
_TWILIO_TOKEN     = os.environ.get("TWILIO_AUTH_TOKEN", "")
_TWILIO_MSID      = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "")
_VOICE_RELAY_URL  = os.environ.get("VOICE_RELAY_URL", "")   # wss://your-host/voice/relay
_VOICE_TTS_VOICE  = os.environ.get("VOICE_TTS_VOICE", "en-US-Neural2-F")
_INTERNAL_KEY     = os.environ.get("INTERNAL_API_KEY", "")

_CFG_PATH = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"

app = FastAPI(title="Agent 10 — Voice", version="0.1.0")


def _load_cfg() -> dict:
    return _yaml.safe_load(_CFG_PATH.read_text())


def _lead_by_phone(db, phone: str) -> dict | None:
    """Look up a lead by phone number (E.164 or local)."""
    row = db.execute(
        text("""
            SELECT id, address, city, owner_name, status, offer_amount
            FROM leads l
            LEFT JOIN LATERAL (
                SELECT offer_amount FROM deals WHERE lead_id = l.id
                ORDER BY created_at DESC LIMIT 1
            ) d ON true
            WHERE l.phone = :phone OR l.phone = :phone_local
            ORDER BY l.updated_at DESC
            LIMIT 1
        """),
        {"phone": phone, "phone_local": phone.lstrip("+1")},
    ).fetchone()
    return dict(row._mapping) if row else None


def _log_call(
    db,
    lead_id: int | None,
    call_sid: str,
    from_number: str,
    direction: str,
    disclosure_version: str,
) -> None:
    db.execute(
        text("""
            INSERT INTO outreach_log
                (lead_id, message, channel, direction, status,
                 twilio_sid, from_number, to_number, disclosure_version)
            VALUES
                (:lid, :msg, 'voice', :dir, 'sent', :sid, :from, :to, :ver)
        """),
        {
            "lid":  lead_id,
            "msg":  f"ConversationRelay call {call_sid}",
            "dir":  direction,
            "sid":  call_sid,
            "from": from_number,
            "to":   _OPERATOR_PHONE if direction == "outbound" else from_number,
            "ver":  disclosure_version,
        },
    )


# ── TwiML helpers ──────────────────────────────────────────────────────────────

def _twiml_conversation_relay(welcome_greeting: str, system_prompt: str) -> str:
    """
    Build the TwiML response to connect an inbound call to ConversationRelay.

    The welcomeGreeting is spoken verbatim by the TTS engine before the LLM
    takes over — this is where the code-rendered disclosures are delivered.
    """
    if not _VOICE_RELAY_URL:
        # No relay URL configured — fall back to a recorded message and hang up
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response>'
            '<Say voice="' + _VOICE_TTS_VOICE + '">' + _xml_escape(welcome_greeting) + '</Say>'
            '<Say>We are unable to connect you right now. Please call back later or send a text message.</Say>'
            '<Hangup/>'
            '</Response>'
        )

    # Embed the system prompt as a JSON parameter on the ConversationRelay element
    sp_json = json.dumps(system_prompt).replace('"', "&quot;")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        '<Connect>'
        f'<ConversationRelay url="{_VOICE_RELAY_URL}"'
        f' welcomeGreeting="{_xml_escape(welcome_greeting)}"'
        f' voice="{_VOICE_TTS_VOICE}"'
        f' transcriptionProvider="deepgram"'
        f' recordingStatusCallback="/voice/status"'
        f' record="true"'
        f' parameters=\'{{"system_prompt": {sp_json}}}\''
        '/>'
        '</Connect>'
        '</Response>'
    )


def _twiml_dial_operator() -> str:
    if not _OPERATOR_PHONE:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Response><Say>The operator is unavailable. Please try again later.</Say><Hangup/></Response>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Dial>{_xml_escape(_OPERATOR_PHONE)}</Dial></Response>'
    )


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.post("/voice/inbound")
async def voice_inbound(
    request: Request,
    CallSid: str = Form(default=""),
    From: str    = Form(default=""),
    To: str      = Form(default=""),
):
    """
    TwiML webhook: called by Twilio when an inbound seller call arrives.

    Returns TwiML that connects the call to ConversationRelay with the
    code-rendered opening (disclosures first).
    """
    caller_phone = From or ""

    with session_ctx() as db:
        lead = _lead_by_phone(db, caller_phone) if caller_phone else None
        lead_context = dict(lead) if lead else None

        try:
            opening, disclosure_version = build_opening(lead_context, call_type="inbound")
        except RuntimeError as exc:
            log.error("Opening script failed: %s — forwarding to operator", exc)
            return Response(content=_twiml_dial_operator(), media_type="application/xml")

        system_prompt = build_system_prompt(lead_context)
        twiml = _twiml_conversation_relay(opening, system_prompt)

        lead_id = lead_context["id"] if lead_context else None
        _log_call(db, lead_id, CallSid, caller_phone, "inbound", disclosure_version)
        db.commit()

        log_agent_event(
            source_agent="voice",
            target_agent="conversationrelay",
            payload={"event_type": "INBOUND_CALL", "call_sid": CallSid,
                     "from": caller_phone, "lead_id": lead_id},
            response={}, status_code=200,
        )

    return Response(content=twiml, media_type="application/xml")


@app.post("/voice/status")
async def voice_status(request: Request):
    """Twilio call status callback — logs call completion."""
    form = await request.form()
    call_sid    = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")
    duration    = form.get("CallDuration", "0")
    log.info("Call %s status=%s duration=%ss", call_sid, call_status, duration)
    log_agent_event(
        source_agent="voice",
        target_agent="operator",
        payload={"event_type": "CALL_STATUS", "call_sid": call_sid,
                 "status": call_status, "duration_s": int(duration or 0)},
        response={}, status_code=200,
    )
    return PlainTextResponse("OK")


class CallbackRequest(BaseModel):
    lead_id:    int   = Field(..., description="Lead to call back")
    reason:     str   = Field("seller_requested", description="'seller_requested' or 'operator_requested'")
    requested_by: str = Field("operator")


@app.post("/voice/callback-request")
def request_callback(body: CallbackRequest):
    """
    Schedule a callback to a seller.

    ALLOWED reasons:
      - 'seller_requested': seller asked us to call them back
      - 'operator_requested': operator manually triggers a follow-up call

    NOT ALLOWED: autonomous cold-call outbound. If `reason` does not match
    the allowed list, the request is rejected (fail closed).

    In dry-run mode: logs the intended call and returns without dialing.
    """
    allowed_reasons = {"seller_requested", "operator_requested"}
    if body.reason not in allowed_reasons:
        raise HTTPException(
            status_code=400,
            detail=f"Callback reason '{body.reason}' not allowed. "
                   f"Only seller_requested or operator_requested are permitted. "
                   f"No cold AI-voice outbound calls.",
        )

    with session_ctx() as db:
        row = db.execute(
            text("""
                SELECT l.id, l.phone, l.address, l.city, l.owner_name,
                       d.offer_amount
                FROM leads l
                LEFT JOIN LATERAL (
                    SELECT offer_amount FROM deals WHERE lead_id = l.id
                    ORDER BY created_at DESC LIMIT 1
                ) d ON true
                WHERE l.id = :lid
            """),
            {"lid": body.lead_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Lead not found")

        lead_context = dict(row._mapping)
        phone = lead_context.get("phone") or ""
        if not phone:
            raise HTTPException(status_code=422, detail="Lead has no phone number on file")

        try:
            opening, disclosure_version = build_opening(lead_context, call_type="callback")
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=f"Opening script failed: {exc}")

        if _SYSTEM_MODE == "dry_run":
            log.info("[DRY-RUN] Callback to %s for lead #%d: %s", phone, body.lead_id, opening[:60])
            call_sid = "dry_run"
        else:
            if not all([_TWILIO_SID, _TWILIO_TOKEN, _TWILIO_MSID]):
                raise HTTPException(status_code=503, detail="Twilio env vars not configured")
            try:
                from twilio.rest import Client
                client = Client(_TWILIO_SID, _TWILIO_TOKEN)
                system_prompt = build_system_prompt(lead_context)
                twiml_url = os.environ.get("VOICE_INBOUND_URL", "")
                call = client.calls.create(
                    to=phone,
                    from_=_OPERATOR_PHONE,   # callbacks use direct from, not MSID
                    url=twiml_url,
                    status_callback="/voice/status",
                )
                call_sid = call.sid
            except Exception as exc:
                log.error("Callback call failed: %s", exc)
                raise HTTPException(status_code=502, detail=str(exc))

        _log_call(db, body.lead_id, call_sid, phone, "outbound", disclosure_version)
        db.commit()
        log_agent_event(
            source_agent="voice",
            target_agent="operator",
            payload={"event_type": "CALLBACK_INITIATED", "lead_id": body.lead_id,
                     "reason": body.reason, "call_sid": call_sid},
            response={}, status_code=200,
        )

    return {"status": "initiated", "call_sid": call_sid, "to": phone}


@app.get("/")
def health():
    return {"status": "ok", "agent": "voice"}


def main() -> None:
    import uvicorn
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [voice] %(message)s",
    )
    uvicorn.run(
        "agents.voice.run:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8010")),
        reload=False,
    )


if __name__ == "__main__":
    main()
