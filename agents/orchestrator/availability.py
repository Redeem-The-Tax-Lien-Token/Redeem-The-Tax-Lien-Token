"""
Gate-availability logic for Agent 0 (Orchestrator) — ADR-003.

Pure module: no database access, no I/O except loading the YAML config.
All datetime inputs must be timezone-aware.

Key rule:
  A gate never times out into approval.  Silence = no action.
  Any deadline that falls inside an unavailable window must be escalated
  at the escalation_cutoff on the last available day before the window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "operator_availability.yaml"

_DAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


@dataclass(frozen=True)
class _Window:
    start_dow: int   # 0=Mon
    start_hour: int
    start_minute: int
    end_dow: int
    end_hour: int
    end_minute: int


@dataclass(frozen=True)
class _EscalationCutoff:
    dow: int
    hour: int
    minute: int


def _load_config(path: Path = _CONFIG_PATH) -> tuple[ZoneInfo, list[_Window], _EscalationCutoff, int]:
    raw = yaml.safe_load(path.read_text())
    tz = ZoneInfo(raw["timezone"])

    windows: list[_Window] = []
    for w in raw.get("unavailable_windows", []):
        sh, sm = (int(x) for x in w["start_time"].split(":"))
        eh, em = (int(x) for x in w["end_time"].split(":"))
        windows.append(_Window(
            start_dow=_DAY_NAMES[w["start_day"].lower()],
            start_hour=sh, start_minute=sm,
            end_dow=_DAY_NAMES[w["end_day"].lower()],
            end_hour=eh, end_minute=em,
        ))

    ec = raw["escalation_cutoff"]
    ech, ecm = (int(x) for x in ec["time"].split(":"))
    cutoff = _EscalationCutoff(
        dow=_DAY_NAMES[ec["day_of_week"].lower()],
        hour=ech, minute=ecm,
    )

    alert_hours = int(raw.get("deadline_alert_hours", 72))
    return tz, windows, cutoff, alert_hours


class AvailabilityChecker:
    """
    Checks operator availability and computes escalation targets.

    All public methods accept and return timezone-aware datetimes.
    Internally everything is converted to Indianapolis local time.
    """

    def __init__(self, config_path: Path = _CONFIG_PATH) -> None:
        self.tz, self._windows, self._cutoff, self.alert_hours = _load_config(config_path)

    def is_unavailable(self, dt: datetime) -> bool:
        """Return True if dt falls inside any configured unavailable window."""
        local = dt.astimezone(self.tz)
        dow   = local.weekday()
        h, m  = local.hour, local.minute

        for w in self._windows:
            if w.start_dow <= w.end_dow:
                # Simple case: window doesn't wrap a week boundary
                if dow < w.start_dow or dow > w.end_dow:
                    continue
                if dow == w.start_dow and (h, m) < (w.start_hour, w.start_minute):
                    continue
                if dow == w.end_dow and (h, m) > (w.end_hour, w.end_minute):
                    continue
                return True
            else:
                # Window wraps (e.g., Sunday → Tuesday)
                if dow > w.start_dow or dow < w.end_dow:
                    return True
                if dow == w.start_dow and (h, m) >= (w.start_hour, w.start_minute):
                    return True
                if dow == w.end_dow and (h, m) <= (w.end_hour, w.end_minute):
                    return True

        return False

    def escalation_target(self, deadline: datetime) -> datetime:
        """
        Return the escalation_cutoff slot immediately before the unavailable
        window that contains `deadline`.

        Only call this when is_unavailable(deadline) is True.
        The returned datetime is in Indianapolis local time.

        Algorithm: walk backward from deadline to find the most-recent
        occurrence of the cutoff day+time before the window starts.
        """
        local = deadline.astimezone(self.tz)

        # Find the window start that contains this deadline
        for w in self._windows:
            # Determine how many days to go back to reach window start
            days_since_window_start = (local.weekday() - w.start_dow) % 7
            window_start = local - timedelta(days=days_since_window_start)
            window_start = window_start.replace(
                hour=w.start_hour, minute=w.start_minute, second=0, microsecond=0
            )

            if local >= window_start:
                # Find the most-recent cutoff day before window_start
                days_back = (w.start_dow - self._cutoff.dow) % 7
                if days_back == 0:
                    days_back = 7
                cutoff_date = window_start - timedelta(days=days_back)
                return cutoff_date.replace(
                    hour=self._cutoff.hour, minute=self._cutoff.minute,
                    second=0, microsecond=0,
                )

        # Fallback: one week back from deadline at cutoff time
        days_back = (local.weekday() - self._cutoff.dow) % 7 or 7
        cutoff_date = local - timedelta(days=days_back)
        return cutoff_date.replace(
            hour=self._cutoff.hour, minute=self._cutoff.minute,
            second=0, microsecond=0,
        )

    def needs_escalation_alert(self, deadline: datetime, now: datetime) -> bool:
        """
        Return True if an escalation alert should be sent right now.

        True when:
          - deadline is inside an unavailable window, AND
          - now is within alert_hours of the escalation_target
            (or past it — meaning we're already late to alert)
        """
        if not self.is_unavailable(deadline):
            return False
        esc = self.escalation_target(deadline)
        return now.astimezone(self.tz) >= esc - timedelta(hours=self.alert_hours)

    def next_available_after(self, dt: datetime) -> datetime:
        """Return the first available moment at or after dt (Monday 00:00)."""
        local = dt.astimezone(self.tz)
        if not self.is_unavailable(local):
            return local
        for w in self._windows:
            days_since_start = (local.weekday() - w.start_dow) % 7
            window_start = local - timedelta(days=days_since_start)
            window_start = window_start.replace(
                hour=w.start_hour, minute=w.start_minute, second=0, microsecond=0
            )
            if local >= window_start:
                days_to_end = (w.end_dow - w.start_dow) % 7
                window_end = window_start + timedelta(days=days_to_end)
                window_end = window_end.replace(
                    hour=w.end_hour, minute=w.end_minute, second=0, microsecond=0
                )
                return (window_end + timedelta(minutes=1)).replace(second=0, microsecond=0)
        return local
