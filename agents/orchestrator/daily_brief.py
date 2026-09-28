"""
Daily brief builder for Agent 0 (Orchestrator).

Compiles the once-per-day summary sent to the operator via SMS and written
to the dashboard.  Content per §7:
  - New HOT leads since last brief
  - Gate A/B/C queue counts
  - Deadlines in the next 72 h
  - Capital position
  - Errors from agent_runs in the last 24 h
  - AI spend in the last 24 h

The brief is sent when:
  1. It is at or after BRIEF_HOUR (default 08:00) in Indianapolis local time.
  2. No brief has been sent today yet (checked via agent_events).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from .capital_pool import CapitalPool

_IND_TZ   = ZoneInfo("America/Indiana/Indianapolis")
_BRIEF_HOUR = int(os.environ.get("DAILY_BRIEF_HOUR", "8"))
_SYSTEM_MODE = os.environ.get("SYSTEM_MODE", "live")

# Gate states visible in each queue
_GATE_A_STATES = ("offer_ready", "strategy_switch")
_GATE_B_STATES = ("buyer_selected", "clear_to_close_b", "appraised")
_GATE_C_STATES = ("scope_ready",)


@dataclass
class DailyBrief:
    generated_at:        datetime
    new_hot_leads:       int
    gate_a_queue:        list[dict]
    gate_b_queue:        list[dict]
    gate_c_queue:        list[dict]
    deadlines_72h:       list[dict]
    capital:             CapitalPool
    agent_errors_24h:    list[dict]
    ai_spend_24h_usd:    float
    overdue_leads:       int
    overdue_deals:       int

    @property
    def gate_total(self) -> int:
        return len(self.gate_a_queue) + len(self.gate_b_queue) + len(self.gate_c_queue)

    def to_dict(self) -> dict:
        return {
            "generated_at":     self.generated_at.isoformat(),
            "new_hot_leads":    self.new_hot_leads,
            "gate_a_count":     len(self.gate_a_queue),
            "gate_b_count":     len(self.gate_b_queue),
            "gate_c_count":     len(self.gate_c_queue),
            "gate_total":       self.gate_total,
            "deadlines_72h":    self.deadlines_72h,
            "capital":          self.capital.summary,
            "agent_errors_24h": self.agent_errors_24h,
            "ai_spend_24h_usd": round(self.ai_spend_24h_usd, 4),
            "overdue_leads":    self.overdue_leads,
            "overdue_deals":    self.overdue_deals,
        }


def _fmt_currency(v: float) -> str:
    return f"${v:,.0f}"


def format_sms(brief: DailyBrief) -> str:
    """
    Format a concise SMS for the operator.
    Kept under 480 chars (2 SMS segments) for reliable delivery.
    """
    parts: list[str] = []
    parts.append(f"REDEEM Daily {brief.generated_at.strftime('%m/%d')}")

    if brief.new_hot_leads:
        parts.append(f"HOT leads: {brief.new_hot_leads}")

    if brief.gate_total:
        parts.append(
            f"Gates: A={len(brief.gate_a_queue)} B={len(brief.gate_b_queue)} "
            f"C={len(brief.gate_c_queue)}"
        )

    if brief.deadlines_72h:
        parts.append(f"Deadlines 72h: {len(brief.deadlines_72h)}")

    cap = brief.capital
    parts.append(
        f"Capital: avail {_fmt_currency(cap.available)} | "
        f"deployed {_fmt_currency(cap.total_deployed)}"
    )

    if brief.overdue_leads or brief.overdue_deals:
        parts.append(f"Overdue: {brief.overdue_leads}L {brief.overdue_deals}D")

    if brief.agent_errors_24h:
        parts.append(f"Errors 24h: {len(brief.agent_errors_24h)}")

    if brief.ai_spend_24h_usd:
        parts.append(f"AI spend: ${brief.ai_spend_24h_usd:.2f}")

    return " | ".join(parts)


def was_brief_sent_today(db: Session, now: datetime) -> bool:
    """Check agent_events for a DAILY_BRIEF event sent today (Indianapolis local)."""
    local_today = now.astimezone(_IND_TZ).date()
    row = db.execute(
        text("""
            SELECT 1 FROM agent_events
            WHERE source_agent = 'orchestrator'
              AND target_agent  = 'operator'
              AND payload->>'event_type' = 'DAILY_BRIEF'
              AND created_at >= :day_start
            LIMIT 1
        """),
        {"day_start": datetime(local_today.year, local_today.month, local_today.day,
                               tzinfo=_IND_TZ)},
    ).fetchone()
    return row is not None


def is_brief_time(now: datetime) -> bool:
    """Return True if it is the daily brief window (at or after BRIEF_HOUR local time)."""
    return now.astimezone(_IND_TZ).hour >= _BRIEF_HOUR


def build_brief(db: Session, now: datetime, capital: CapitalPool, alert_hours: int = 72) -> DailyBrief:
    """
    Build a DailyBrief by querying the DB.
    All datetime comparisons use `now` so this is testable without mocking time.
    """
    since_24h = now - timedelta(hours=24)

    # New HOT leads in the last 24 h
    new_hot = db.execute(
        text("SELECT COUNT(*) FROM leads WHERE status = 'hot' AND updated_at >= :since"),
        {"since": since_24h},
    ).scalar() or 0

    # Gate A queue
    gate_a = [
        dict(r._mapping)
        for r in db.execute(
            text("SELECT id, status, updated_at FROM leads WHERE status IN :states ORDER BY updated_at"),
            {"states": tuple(_GATE_A_STATES)},
        ).fetchall()
    ]

    # Gate B queue (deals)
    gate_b = [
        dict(r._mapping)
        for r in db.execute(
            text("SELECT id, status, updated_at FROM deals WHERE status IN :states ORDER BY updated_at"),
            {"states": tuple(_GATE_B_STATES)},
        ).fetchall()
    ]

    # Gate C queue (deals)
    gate_c = [
        dict(r._mapping)
        for r in db.execute(
            text("SELECT id, status, updated_at FROM deals WHERE status IN :states ORDER BY updated_at"),
            {"states": tuple(_GATE_C_STATES)},
        ).fetchall()
    ]

    # Deadlines in the next 72 h: leads overdue within the alert window
    # Using sla_hours from config is complex in SQL; approximate with raw query
    alert_cutoff = now + timedelta(hours=alert_hours)
    deadlines: list[dict] = []
    for row in db.execute(
        text("""
            SELECT 'lead' AS entity_type, id, status, updated_at
            FROM leads
            WHERE status NOT IN ('dead', 'dnc', 'nurture', 'new', 'scored',
                                  'skip_traced', 'outreach_active', 'offer_ready',
                                  'strategy_switch', 'stabilized', 'fee_received')
              AND updated_at <= :cutoff
        """),
        {"cutoff": alert_cutoff},
    ).fetchall():
        deadlines.append(dict(row._mapping))

    # Agent errors in the last 24 h
    errors = [
        {"agent": r.agent_name, "error": r.error, "at": r.finished_at.isoformat() if r.finished_at else None}
        for r in db.execute(
            text("""
                SELECT agent_name, error, finished_at
                FROM agent_runs
                WHERE error IS NOT NULL
                  AND started_at >= :since
                ORDER BY started_at DESC
                LIMIT 20
            """),
            {"since": since_24h},
        ).fetchall()
    ]

    # AI spend in the last 24 h
    spend = db.execute(
        text("SELECT COALESCE(SUM(cost_usd), 0) FROM agent_runs WHERE started_at >= :since"),
        {"since": since_24h},
    ).scalar() or 0.0

    # Overdue counts (very rough: updated_at > 48h ago for non-gate non-terminal states)
    overdue_leads = db.execute(
        text("""
            SELECT COUNT(*) FROM leads
            WHERE status NOT IN ('dead', 'dnc', 'offer_ready', 'strategy_switch',
                                  'new', 'scored', 'skip_traced', 'outreach_active',
                                  'stabilized', 'fee_received')
              AND updated_at < :threshold
        """),
        {"threshold": now - timedelta(hours=48)},
    ).scalar() or 0

    overdue_deals = db.execute(
        text("""
            SELECT COUNT(*) FROM deals
            WHERE status NOT IN ('dead', 'buyer_selected', 'clear_to_close_b',
                                  'appraised', 'scope_ready', 'stabilized',
                                  'fee_received', 'hold_as_is', 'sell_retail',
                                  'refinanced')
              AND updated_at < :threshold
        """),
        {"threshold": now - timedelta(hours=48)},
    ).scalar() or 0

    return DailyBrief(
        generated_at=now,
        new_hot_leads=int(new_hot),
        gate_a_queue=gate_a,
        gate_b_queue=gate_b,
        gate_c_queue=gate_c,
        deadlines_72h=deadlines,
        capital=capital,
        agent_errors_24h=errors,
        ai_spend_24h_usd=float(spend),
        overdue_leads=int(overdue_leads),
        overdue_deals=int(overdue_deals),
    )
