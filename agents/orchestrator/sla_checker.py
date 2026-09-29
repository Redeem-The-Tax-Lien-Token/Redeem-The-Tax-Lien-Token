"""
SLA deadline computation for Agent 0 (Orchestrator).

Loads config/sla.yaml and provides:
  - sla_hours(status) — hours from updated_at before a state is overdue
  - compute_deadline(status, updated_at) — the overdue timestamp (None for gate states)
  - is_overdue(status, updated_at, now) — True when SLA has elapsed
  - alert_threshold(status, updated_at, alert_hours) — send alert this many hours before

Gate states have sla_hours=0; compute_deadline returns None for them.
The Orchestrator shows gate items in the queue regardless of time elapsed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

import yaml

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "sla.yaml"
_DEFAULT_SLA_HOURS = 24   # fallback for states not listed in sla.yaml


@lru_cache(maxsize=1)
def _load_sla(path: str) -> dict[str, dict]:
    """Return sla.yaml['states'] keyed by uppercase status."""
    raw = yaml.safe_load(Path(path).read_text())
    return {k.upper(): v for k, v in raw.get("states", {}).items()}


def _sla_cfg(config_path: Path = _CONFIG_PATH) -> dict[str, dict]:
    return _load_sla(str(config_path))


class SLAChecker:
    """
    Computes SLA deadlines from config/sla.yaml.

    Args:
        config_path: Override path to sla.yaml (for testing).
    """

    # Gate states never timeout — they show in the Gate queue forever
    # until the operator acts.  SLA hours = 0 in the YAML.
    GATE_STATES: ClassVar[frozenset[str]] = frozenset({
        "OFFER_READY", "STRATEGY_SWITCH",
        "BUYER_SELECTED", "CLEAR_TO_CLOSE_B", "APPRAISED",
        "SCOPE_READY",
    })

    def __init__(self, config_path: Path = _CONFIG_PATH) -> None:
        self._cfg = _sla_cfg(config_path)

    def sla_hours(self, status: str) -> int:
        """Hours before a state is considered overdue (0 = gate state)."""
        entry = self._cfg.get(status.upper())
        if entry is None:
            return _DEFAULT_SLA_HOURS
        return int(entry.get("sla_hours", _DEFAULT_SLA_HOURS))

    def owner_agent(self, status: str) -> str:
        """Which agent owns this state."""
        entry = self._cfg.get(status.upper())
        if entry is None:
            return "orchestrator"
        return str(entry.get("owner_agent", "orchestrator"))

    def escalation_text(self, status: str) -> str:
        """Human-readable escalation guidance for a state."""
        entry = self._cfg.get(status.upper())
        if entry is None:
            return "Unknown state — investigate"
        return str(entry.get("escalation", "Escalate to operator"))

    def is_gate_state(self, status: str) -> bool:
        return status.upper() in self.GATE_STATES

    def compute_deadline(self, status: str, updated_at: datetime) -> datetime | None:
        """
        Return the SLA deadline datetime for a lead/deal in this status.
        Returns None for gate states (no automatic timeout — human must act).
        """
        hours = self.sla_hours(status)
        if hours == 0:
            return None
        return updated_at + timedelta(hours=hours)

    def is_overdue(self, status: str, updated_at: datetime, now: datetime) -> bool:
        """True if the SLA deadline has elapsed."""
        deadline = self.compute_deadline(status, updated_at)
        if deadline is None:
            return False   # gate states are never "overdue", just pending
        return now >= deadline

    def hours_overdue(self, status: str, updated_at: datetime, now: datetime) -> float:
        """Hours past the SLA deadline (negative means time remains)."""
        deadline = self.compute_deadline(status, updated_at)
        if deadline is None:
            return 0.0
        return (now - deadline).total_seconds() / 3600

    def alert_threshold(self, status: str, updated_at: datetime, alert_hours: int) -> datetime | None:
        """
        Datetime at which a proactive alert should fire (alert_hours before deadline).
        Returns None for gate states.
        """
        deadline = self.compute_deadline(status, updated_at)
        if deadline is None:
            return None
        return deadline - timedelta(hours=alert_hours)
