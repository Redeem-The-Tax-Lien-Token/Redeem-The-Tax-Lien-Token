"""
Agent 9 — Nurture (FastAPI service).

Runs on a schedule (Orchestrator dispatches or cron). One call to /run
processes all eligible leads and sends the next cadence touch.

Contract (§7, Agent 9):
  - Every message carries the HB 1068 solicitation disclosure, inserted by
    build_seller_sms() — never by the LLM or by this module directly.
  - Compliance linter runs before every send (fail closed on lint failure).
  - Never negotiates — templates contain no price language, no counter-offers.
  - Re-underwriting: if the reply contains new signal (handled by Agent 4),
    Agent 4 transitions the lead; this agent only processes stable statuses.
  - SYSTEM_MODE=dry_run → all sends go to log, not Twilio.
  - TCPA quiet hours enforced (08:00–21:00 Indianapolis local time).
  - A response at any point resets the lead to Agent 4 / intake flow.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from shared.db import session_ctx, log_agent_event
from shared.compliance.disclosures import build_seller_sms
from shared.compliance.linter import check_outbound
from shared.state_machine import transition_lead

from .cadence import cadence_due, should_expire_to_dead, _ELIGIBLE_STATUSES
from .templates import render_template

import yaml as _yaml

log = logging.getLogger(__name__)

_SYSTEM_MODE  = os.environ.get("SYSTEM_MODE", "live")
_TWILIO_SID   = os.environ.get("TWILIO_ACCOUNT_SID", "")
_TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
_TWILIO_MSID  = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "")

_CFG_PATH = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"

_IND_TZ_NAME = "America/Indiana/Indianapolis"

app = FastAPI(title="Agent 9 — Nurture", version="0.1.0")


def _load_cfg() -> dict:
    import yaml
    return yaml.safe_load(_CFG_PATH.read_text())


def _is_quiet_hours() -> bool:
    from zoneinfo import ZoneInfo
    now_local = datetime.now(tz=ZoneInfo(_IND_TZ_NAME))
    return not (8 <= now_local.hour < 21)


def _send_sms(to: str, body: str) -> str | None:
    if _SYSTEM_MODE == "dry_run":
        log.info("[DRY-RUN] SMS to %s: %s", to, body[:80])
        return "dry_run"
    if not all([_TWILIO_SID, _TWILIO_TOKEN, _TWILIO_MSID, to]):
        log.warning("SMS skipped — Twilio env vars missing")
        return None
    try:
        from twilio.rest import Client
        client = Client(_TWILIO_SID, _TWILIO_TOKEN)
        msg = client.messages.create(
            messaging_service_sid=_TWILIO_MSID,
            to=to,
            body=body,
        )
        return msg.sid
    except Exception as exc:
        log.error("SMS send failed: %s", exc)
        return None


def _fetch_eligible_leads(db) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT id, status, address, city, owner_name, phone,
                   manual_override_at, updated_at
            FROM leads
            WHERE status IN ('warm', 'cold', 'nurture', 'offer_declined')
              AND phone IS NOT NULL
              AND (manual_override_at IS NULL
                   OR manual_override_at < NOW() - INTERVAL '24 hours')
            ORDER BY updated_at ASC
        """)
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def _touch_count_and_last(db, lead_id: int) -> tuple[int, datetime | None]:
    """Return (nurture_touch_count, last_touch_at) for this lead."""
    row = db.execute(
        text("""
            SELECT COUNT(*) AS cnt, MAX(sent_at) AS last_at
            FROM outreach_log
            WHERE lead_id   = :lid
              AND direction  = 'outbound'
              AND channel    = 'sms'
              AND message LIKE '%nurture%'
               OR (lead_id = :lid AND direction = 'outbound' AND channel = 'sms'
                   AND sent_at >= (
                       SELECT COALESCE(MAX(sent_at), '2000-01-01')
                       FROM outreach_log
                       WHERE lead_id = :lid AND direction = 'inbound'
                   ))
        """),
        {"lid": lead_id},
    ).fetchone()
    if row is None:
        return 0, None
    cnt = int(row.cnt or 0)
    last_at = row.last_at
    return cnt, last_at


def _touch_stats(db, lead_id: int) -> tuple[int, datetime | None]:
    """
    Simpler version: count all outbound nurture-window SMSes and get the last one.
    The nurture window is defined as outbound SMSes after the lead entered a nurture status.
    """
    row = db.execute(
        text("""
            SELECT COUNT(*) AS cnt, MAX(sent_at) AS last_at
            FROM outreach_log
            WHERE lead_id  = :lid
              AND direction = 'outbound'
              AND channel   = 'sms'
        """),
        {"lid": lead_id},
    ).fetchone()
    if row is None:
        return 0, None
    # Subtract the 3 initial outreach touches (touch 1-3 are from Agent 3)
    total = max(0, int(row.cnt or 0) - 3)
    return total, row.last_at


class RunRequest(BaseModel):
    max_leads: int = Field(50, description="Max leads to process in one run")
    dry_run:   bool = Field(False, description="Override SYSTEM_MODE for this call only")


class RunResponse(BaseModel):
    sent:              int
    skipped_quiet:     int
    skipped_cadence:   int
    skipped_lint:      int
    expired_to_dead:   int
    errors:            int
    dry_run:           bool


@app.post("/run", response_model=RunResponse)
def run_nurture(req: RunRequest):
    """
    Process one nurture batch: send the next cadence touch to all eligible leads.
    """
    cfg      = _load_cfg()
    is_dry   = (req.dry_run or _SYSTEM_MODE == "dry_run")
    now      = datetime.now(tz=timezone.utc)

    sent = skipped_quiet = skipped_cadence = skipped_lint = expired = errors = 0

    if _is_quiet_hours() and not is_dry:
        log.info("Nurture skipped — quiet hours")
        return RunResponse(sent=0, skipped_quiet=1, skipped_cadence=0,
                           skipped_lint=0, expired_to_dead=0, errors=0, dry_run=is_dry)

    with session_ctx() as db:
        leads = _fetch_eligible_leads(db)[:req.max_leads]

        for lead in leads:
            lead_id = lead["id"]
            status  = lead["status"]
            phone   = lead.get("phone") or ""

            if not phone:
                continue

            touch_count, last_touch_at = _touch_stats(db, lead_id)

            # Check if cadence has expired → transition to dead
            if should_expire_to_dead(touch_count, cfg):
                try:
                    transition_lead(lead_id, "dead", actor="nurture",
                                    reason="nurture cadence exhausted", db=db)
                    expired += 1
                except Exception as exc:
                    log.error("Expire to dead failed lead #%d: %s", lead_id, exc)
                    errors += 1
                continue

            result = cadence_due(lead, last_touch_at, touch_count, cfg, now)
            if not result.due:
                skipped_cadence += 1
                continue

            # Render raw body (no disclosure yet)
            first_name = (lead.get("owner_name") or "").split()[0] if lead.get("owner_name") else None
            raw_body = render_template(
                status_bucket=status,
                touch_number=result.touch_number,
                first_name=first_name,
                address=lead.get("address") or "your property",
                city=lead.get("city") or "Indianapolis",
            )

            # Inject HB 1068 disclosure + TCPA opt-out (code, never LLM)
            try:
                full_msg, disclosure_version = build_seller_sms(raw_body)
            except RuntimeError as exc:
                log.error("COMPLIANCE_BLOCK lead #%d: %s", lead_id, exc)
                skipped_lint += 1
                continue

            # Compliance linter
            lint = check_outbound(full_msg, disclosure_version)
            if not lint.ok:
                log.error("COMPLIANCE_BLOCK lead #%d: %s", lead_id, lint.reason)
                skipped_lint += 1
                continue

            # Send
            sid = None
            if not is_dry:
                sid = _send_sms(phone, full_msg)
                if sid is None:
                    errors += 1
                    continue
            else:
                log.info("[DRY-RUN] Nurture touch #%d to lead #%d", result.touch_number, lead_id)
                sid = "dry_run"

            # Log to outreach_log with disclosure_version
            db.execute(
                text("""
                    INSERT INTO outreach_log
                        (lead_id, message, channel, direction, status,
                         twilio_sid, to_number, disclosure_version)
                    VALUES
                        (:lid, :msg, 'sms', 'outbound', :status,
                         :sid, :to, :ver)
                """),
                {
                    "lid":    lead_id,
                    "msg":    full_msg,
                    "status": "sent" if not is_dry else "dry_run",
                    "sid":    sid,
                    "to":     phone,
                    "ver":    disclosure_version,
                },
            )
            db.commit()
            sent += 1

        log_agent_event(
            source_agent="nurture",
            target_agent="operator",
            payload={"event_type": "NURTURE_RUN", "sent": sent, "expired": expired},
            response={}, status_code=200,
        )

    return RunResponse(
        sent=sent, skipped_quiet=skipped_quiet, skipped_cadence=skipped_cadence,
        skipped_lint=skipped_lint, expired_to_dead=expired, errors=errors, dry_run=is_dry,
    )


@app.get("/")
def health():
    return {"status": "ok", "agent": "nurture"}


def main() -> None:
    import uvicorn
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [nurture] %(message)s",
    )
    uvicorn.run(
        "agents.nurture.run:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8009")),
        reload=False,
    )


if __name__ == "__main__":
    main()
