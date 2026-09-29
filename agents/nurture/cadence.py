"""
Nurture cadence logic for Agent 9.

Determines whether a lead is due for a nurture touch and what touch number
to send. All logic is deterministic Python — no LLM.

Cadence rules:
  - Touch interval: 7 days between touches (configurable).
  - Maximum touches: 26 (≈6 months); lead → dead after max reached.
  - Eligible statuses: 'warm', 'cold', 'nurture', 'offer_declined'.
  - Skip if: DNC, manual_override_at within last 24h, already touched
    within the interval.
  - A response at any point (via Agent 4 / Intake) resets the lead to
    'responded' and the cadence hands back to the intake flow.

Public API:
    result = cadence_due(lead, last_touch_at, touch_count, cfg)
    # result.due:         bool
    # result.touch_number: int (next touch to send)
    # result.reason:      str
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


_ELIGIBLE_STATUSES = frozenset({"warm", "cold", "nurture", "offer_declined"})
_DEFAULT_INTERVAL_DAYS = 7
_DEFAULT_MAX_TOUCHES   = 26


@dataclass(frozen=True)
class CadenceResult:
    due:          bool
    touch_number: int   # next touch to send (1-based); 0 if not due
    reason:       str   # human-readable reason (for logging)


def cadence_due(
    lead: dict[str, Any],
    last_touch_at: datetime | None,
    touch_count: int,
    cfg: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> CadenceResult:
    """
    Determine if a nurture touch is due for this lead.

    Parameters
    ----------
    lead          : dict from DB (status, manual_override_at, ...)
    last_touch_at : datetime of most recent nurture SMS (None = never touched)
    touch_count   : number of nurture touches already sent
    cfg           : parsed strategy.yaml (reads nurture.interval_days, max_touches)
    now           : injectable for tests; defaults to utcnow()
    """
    nurture_cfg   = (cfg or {}).get("nurture", {})
    interval_days = int(nurture_cfg.get("interval_days", _DEFAULT_INTERVAL_DAYS))
    max_touches   = int(nurture_cfg.get("max_touches", _DEFAULT_MAX_TOUCHES))

    if now is None:
        now = datetime.now(tz=timezone.utc)

    status = (lead.get("status") or "").lower()

    # Terminal / ineligible status
    if status not in _ELIGIBLE_STATUSES:
        return CadenceResult(due=False, touch_number=0,
                             reason=f"status '{status}' not eligible for nurture")

    # Manual override blocks all agents for 24h
    override_at = lead.get("manual_override_at")
    if override_at:
        if isinstance(override_at, str):
            override_at = datetime.fromisoformat(override_at)
        if override_at.tzinfo is None:
            override_at = override_at.replace(tzinfo=timezone.utc)
        if (now - override_at) < timedelta(hours=24):
            return CadenceResult(due=False, touch_number=0, reason="manual_override active")

    # Max touches reached — lead should move to dead
    if touch_count >= max_touches:
        return CadenceResult(due=False, touch_number=0,
                             reason=f"max_touches reached ({touch_count}/{max_touches})")

    # Check interval since last touch
    if last_touch_at is not None:
        if last_touch_at.tzinfo is None:
            last_touch_at = last_touch_at.replace(tzinfo=timezone.utc)
        elapsed = now - last_touch_at
        if elapsed < timedelta(days=interval_days):
            days_left = interval_days - elapsed.days
            return CadenceResult(due=False, touch_number=0,
                                 reason=f"{days_left}d until next touch")

    return CadenceResult(due=True, touch_number=touch_count + 1, reason="cadence due")


def should_expire_to_dead(touch_count: int, cfg: dict[str, Any] | None = None) -> bool:
    """True if the lead has exhausted its nurture cadence and should move to dead."""
    max_touches = int((cfg or {}).get("nurture", {}).get("max_touches", _DEFAULT_MAX_TOUCHES))
    return touch_count >= max_touches
