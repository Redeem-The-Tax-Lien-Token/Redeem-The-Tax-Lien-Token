"""
Agent 0 — Orchestrator (the brain).

Runs every 5 minutes via cron/APScheduler.  One call to orchestrate()
performs a full scan, dispatches work, fires escalation alerts, and
optionally sends the daily brief.

Contract (§7, ADR-003):
  - A gate never times out into approval.  Silence = no action.
  - Deadlines inside unavailable windows are escalated at Thursday 18:00.
  - Never performs compliance or money actions — routes only.
  - Wraps every run in agent_run_ctx() for observability.
  - All SMS dispatched via TWILIO_MESSAGING_SERVICE_SID (never from_=).
  - SYSTEM_MODE=dry_run → all outbound actions go to log, not live.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

# Allow imports from monorepo root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy.orm import Session
from sqlalchemy import text

from shared.db import session_ctx, log_agent_event
from shared.state_machine import GATE_STATES

from .availability import AvailabilityChecker
from .capital_pool import CapitalPool, read_capital_pool
from .daily_brief import (
    DailyBrief, build_brief, format_sms,
    is_brief_time, was_brief_sent_today,
)
from .sla_checker import SLAChecker

log = logging.getLogger(__name__)

_SYSTEM_MODE  = os.environ.get("SYSTEM_MODE", "live")
_OPERATOR_PHONE = os.environ.get("OPERATOR_PHONE", "")
_TWILIO_SID   = os.environ.get("TWILIO_ACCOUNT_SID", "")
_TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
_TWILIO_MSID  = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "")

_GATE_LEAD_STATES = {s for s, g in GATE_STATES.items() if g.startswith("GATE")}
_GATE_DEAL_STATES = {s for s, g in GATE_STATES.items() if g.startswith("GATE")}


# ── SMS ────────────────────────────────────────────────────────────────────────

def _send_sms(to: str, body: str) -> None:
    """Send an SMS to the operator via Twilio Messaging Service."""
    if _SYSTEM_MODE == "dry_run":
        log.info("[DRY-RUN] SMS to %s: %s", to, body)
        return
    if not all([_TWILIO_SID, _TWILIO_TOKEN, _TWILIO_MSID, to]):
        log.warning("SMS skipped — Twilio env vars not set (OPERATOR_PHONE, SID, TOKEN, MSID)")
        return
    try:
        from twilio.rest import Client
        client = Client(_TWILIO_SID, _TWILIO_TOKEN)
        client.messages.create(
            messaging_service_sid=_TWILIO_MSID,
            to=to,
            body=body,
        )
        log.info("SMS sent to %s: %s", to, body[:60])
    except Exception as exc:
        log.error("SMS send failed: %s", exc)


# ── Dispatch ───────────────────────────────────────────────────────────────────

def _dispatch(
    db: Session,
    target_agent: str,
    entity_type: str,
    entity_id: int,
    action: str,
    payload: dict | None = None,
) -> None:
    """
    Signal work to a downstream agent by writing to agent_events.

    In the current phase agents poll agent_events for tasks addressed to them.
    Future: replace with HTTP call once agent endpoints stabilise.
    """
    full_payload = {
        "entity_type": entity_type,
        "entity_id":   entity_id,
        "action":      action,
        **(payload or {}),
    }
    log_agent_event(
        source_agent="orchestrator",
        target_agent=target_agent,
        payload=full_payload,
        response={},
        status_code=202,
    )
    log.debug("Dispatched %s → %s: %s #%s", action, target_agent, entity_type, entity_id)


# ── Escalation alert ───────────────────────────────────────────────────────────

def _already_alerted(db: Session, entity_type: str, entity_id: int, hours: int = 8) -> bool:
    """True if an escalation alert was already sent for this entity recently."""
    row = db.execute(
        text("""
            SELECT 1 FROM agent_events
            WHERE source_agent = 'orchestrator'
              AND target_agent  = 'operator'
              AND payload->>'event_type' = 'ESCALATION'
              AND (payload->>'entity_type')::text = :etype
              AND (payload->>'entity_id')::int     = :eid
              AND created_at >= NOW() - (:hours * INTERVAL '1 hour')
            LIMIT 1
        """),
        {"etype": entity_type, "eid": entity_id, "hours": hours},
    ).fetchone()
    return row is not None


def _send_escalation(
    db: Session,
    entity_type: str,
    entity_id: int,
    status: str,
    reason: str,
    escalation_target: datetime | None,
) -> None:
    """Fire an escalation: write to agent_events and SMS the operator."""
    esc_str = escalation_target.isoformat() if escalation_target else "IMMEDIATE"
    msg = (
        f"REDEEM ESCALATION — {entity_type.upper()} #{entity_id} is {status.upper()}. "
        f"{reason}. Action needed by {esc_str}."
    )
    log_agent_event(
        source_agent="orchestrator",
        target_agent="operator",
        payload={
            "event_type":   "ESCALATION",
            "entity_type":  entity_type,
            "entity_id":    entity_id,
            "status":       status,
            "reason":       reason,
            "escalation_at": esc_str,
        },
        response={},
        status_code=200,
    )
    _send_sms(_OPERATOR_PHONE, msg)
    log.info("Escalation fired for %s #%d (%s)", entity_type, entity_id, status)


# ── DB queries ─────────────────────────────────────────────────────────────────

def _fetch_active_leads(db: Session) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT id, status, updated_at
            FROM leads
            WHERE status NOT IN ('dead', 'dnc', 'stabilized', 'fee_received')
        """)
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def _fetch_active_deals(db: Session) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT id, status, updated_at
            FROM deals
            WHERE status NOT IN ('dead', 'stabilized', 'fee_received',
                                  'hold_as_is', 'sell_retail', 'refinanced')
        """)
    ).fetchall()
    return [dict(r._mapping) for r in rows]


# ── Main orchestration loop ────────────────────────────────────────────────────

def orchestrate(db: Session | None = None) -> dict:
    """
    Run one full orchestration pass.

    Returns a summary dict written to agent_runs.outputs.
    If db is None, opens its own session_ctx.
    """
    now = datetime.now(tz=timezone.utc)
    availability = AvailabilityChecker()
    sla = SLAChecker()

    summary = {
        "overdue_leads":    0,
        "overdue_deals":    0,
        "gate_items":       0,
        "escalations_sent": 0,
        "dispatches":       0,
        "daily_brief_sent": False,
        "errors":           [],
    }

    @contextmanager
    def _db() -> Generator[Session, None, None]:
        if db is not None:
            yield db
        else:
            with session_ctx() as s:
                yield s

    with _db() as session:
        capital = read_capital_pool(session)

        # ── Scan leads ────────────────────────────────────────────────────────
        for lead in _fetch_active_leads(session):
            status     = lead["status"]
            updated_at = lead["updated_at"]
            lead_id    = lead["id"]

            if not updated_at:
                continue

            updated_at_aware = (
                updated_at.replace(tzinfo=timezone.utc)
                if updated_at.tzinfo is None else updated_at
            )

            # Gate state: ensure operator knows it's waiting
            if sla.is_gate_state(status):
                summary["gate_items"] += 1
                # Compute a proxy deadline: updated_at + 72h as alert
                from datetime import timedelta
                proxy_deadline = updated_at_aware + timedelta(hours=72)
                if (
                    availability.needs_escalation_alert(proxy_deadline, now)
                    and not _already_alerted(session, "lead", lead_id)
                ):
                    esc = availability.escalation_target(proxy_deadline)
                    _send_escalation(
                        session, "lead", lead_id, status,
                        sla.escalation_text(status), esc,
                    )
                    summary["escalations_sent"] += 1
                continue

            # SLA overdue check
            if sla.is_overdue(status, updated_at_aware, now):
                summary["overdue_leads"] += 1
                owner = sla.owner_agent(status)
                _dispatch(session, owner, "lead", lead_id, "SLA_OVERDUE",
                          {"status": status, "hours_overdue": round(sla.hours_overdue(status, updated_at_aware, now), 1)})
                summary["dispatches"] += 1

                # Check if SLA deadline falls in unavailable window
                deadline = sla.compute_deadline(status, updated_at_aware)
                if deadline and availability.needs_escalation_alert(deadline, now):
                    if not _already_alerted(session, "lead", lead_id):
                        esc = availability.escalation_target(deadline)
                        _send_escalation(
                            session, "lead", lead_id, status,
                            sla.escalation_text(status), esc,
                        )
                        summary["escalations_sent"] += 1

        # ── Scan deals ────────────────────────────────────────────────────────
        for deal in _fetch_active_deals(session):
            status     = deal["status"]
            updated_at = deal["updated_at"]
            deal_id    = deal["id"]

            if not updated_at:
                continue

            updated_at_aware = (
                updated_at.replace(tzinfo=timezone.utc)
                if updated_at.tzinfo is None else updated_at
            )

            if sla.is_gate_state(status):
                summary["gate_items"] += 1
                from datetime import timedelta
                proxy_deadline = updated_at_aware + timedelta(hours=72)
                if (
                    availability.needs_escalation_alert(proxy_deadline, now)
                    and not _already_alerted(session, "deal", deal_id)
                ):
                    esc = availability.escalation_target(proxy_deadline)
                    _send_escalation(
                        session, "deal", deal_id, status,
                        sla.escalation_text(status), esc,
                    )
                    summary["escalations_sent"] += 1
                continue

            if sla.is_overdue(status, updated_at_aware, now):
                summary["overdue_deals"] += 1
                owner = sla.owner_agent(status)
                _dispatch(session, owner, "deal", deal_id, "SLA_OVERDUE",
                          {"status": status, "hours_overdue": round(sla.hours_overdue(status, updated_at_aware, now), 1)})
                summary["dispatches"] += 1

                deadline = sla.compute_deadline(status, updated_at_aware)
                if deadline and availability.needs_escalation_alert(deadline, now):
                    if not _already_alerted(session, "deal", deal_id):
                        esc = availability.escalation_target(deadline)
                        _send_escalation(
                            session, "deal", deal_id, status,
                            sla.escalation_text(status), esc,
                        )
                        summary["escalations_sent"] += 1

        # ── Daily brief ───────────────────────────────────────────────────────
        if is_brief_time(now) and not was_brief_sent_today(session, now):
            try:
                brief = build_brief(session, now, capital, alert_hours=availability.alert_hours)
                sms_text = format_sms(brief)
                _send_sms(_OPERATOR_PHONE, sms_text)
                log_agent_event(
                    source_agent="orchestrator",
                    target_agent="operator",
                    payload={**brief.to_dict(), "event_type": "DAILY_BRIEF"},
                    response={"sms": sms_text},
                    status_code=200,
                )
                summary["daily_brief_sent"] = True
                log.info("Daily brief sent")
            except Exception as exc:
                err = f"Daily brief failed: {exc}"
                summary["errors"].append(err)
                log.error(err)

    return summary


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [orchestrator] %(message)s",
    )

    # Import here so missing DATABASE_URL only fails when actually running
    from shared.db import agent_run_ctx

    with agent_run_ctx("orchestrator") as run:
        try:
            result = orchestrate()
            run["outputs"] = result
            log.info(
                "Orchestration complete: %d overdue leads, %d overdue deals, "
                "%d gate items, %d escalations, %d dispatches",
                result["overdue_leads"], result["overdue_deals"],
                result["gate_items"], result["escalations_sent"],
                result["dispatches"],
            )
        except Exception as exc:
            run["error"] = repr(exc)
            log.exception("Orchestration failed: %s", exc)
            raise


if __name__ == "__main__":
    main()
