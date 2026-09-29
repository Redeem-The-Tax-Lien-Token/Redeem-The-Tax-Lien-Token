"""
Unit tests for agents/orchestrator/availability.py and sla_checker.py.

All tests are pure Python — no database access.

Coverage goals:
  - AvailabilityChecker: every weekday/time boundary in the window,
    escalation_target computation, needs_escalation_alert, next_available_after
  - SLAChecker: sla_hours lookup, compute_deadline, is_overdue,
    hours_overdue, alert_threshold, gate state detection
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.orchestrator.availability import AvailabilityChecker
from agents.orchestrator.sla_checker import SLAChecker

_IND = ZoneInfo("America/Indiana/Indianapolis")
_UTC = ZoneInfo("UTC")


def _ind(weekday_name: str, hour: int, minute: int = 0) -> datetime:
    """
    Create a timezone-aware Indianapolis datetime on the given weekday.
    Uses a fixed reference week (Mon 2026-09-28 .. Sun 2026-10-04).
    """
    _REF_MON = datetime(2026, 9, 28, tzinfo=_IND)   # Monday
    days = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}
    return (_REF_MON + timedelta(days=days[weekday_name.lower()])).replace(
        hour=hour, minute=minute, second=0, microsecond=0,
    )


# ── AvailabilityChecker ────────────────────────────────────────────────────────

@pytest.fixture
def avail() -> AvailabilityChecker:
    return AvailabilityChecker()


class TestIsUnavailable:
    def test_monday_morning_available(self, avail):
        assert avail.is_unavailable(_ind("monday", 9)) is False

    def test_thursday_evening_available(self, avail):
        assert avail.is_unavailable(_ind("thursday", 17, 59)) is False

    def test_friday_before_cutoff_available(self, avail):
        assert avail.is_unavailable(_ind("friday", 15, 59)) is False

    def test_friday_at_cutoff_unavailable(self, avail):
        assert avail.is_unavailable(_ind("friday", 16, 0)) is True

    def test_friday_evening_unavailable(self, avail):
        assert avail.is_unavailable(_ind("friday", 22, 30)) is True

    def test_saturday_morning_unavailable(self, avail):
        assert avail.is_unavailable(_ind("saturday", 8)) is True

    def test_saturday_midnight_unavailable(self, avail):
        assert avail.is_unavailable(_ind("saturday", 0)) is True

    def test_sunday_noon_unavailable(self, avail):
        assert avail.is_unavailable(_ind("sunday", 12)) is True

    def test_sunday_end_of_day_unavailable(self, avail):
        assert avail.is_unavailable(_ind("sunday", 23, 59)) is True

    def test_utc_input_converted_correctly(self, avail):
        # Indianapolis is UTC-4 in summer (EDT); Friday 16:00 local = Friday 20:00 UTC
        fri_utc = datetime(2026, 10, 2, 20, 0, tzinfo=_UTC)   # 2026-10-02 is a Friday
        assert avail.is_unavailable(fri_utc) is True

    def test_wednesday_all_day_available(self, avail):
        for hour in (0, 6, 12, 18, 23):
            assert avail.is_unavailable(_ind("wednesday", hour)) is False


class TestEscalationTarget:
    def test_deadline_on_saturday_escalates_to_thursday(self, avail):
        sat = _ind("saturday", 14)
        esc = avail.escalation_target(sat)
        # Should be Thursday 18:00 same week
        assert esc.weekday() == 3              # Thursday
        assert esc.hour == 18
        assert esc.minute == 0

    def test_deadline_on_sunday_escalates_to_thursday(self, avail):
        sun = _ind("sunday", 10)
        esc = avail.escalation_target(sun)
        assert esc.weekday() == 3
        assert esc.hour == 18

    def test_deadline_on_friday_evening_escalates_to_thursday(self, avail):
        fri = _ind("friday", 20)
        esc = avail.escalation_target(fri)
        assert esc.weekday() == 3
        assert esc.hour == 18

    def test_escalation_target_is_before_deadline(self, avail):
        sat = _ind("saturday", 12)
        esc = avail.escalation_target(sat)
        assert esc < sat

    def test_escalation_target_timezone_is_indianapolis(self, avail):
        sat = _ind("saturday", 12)
        esc = avail.escalation_target(sat)
        assert esc.tzinfo is not None


class TestNeedsEscalationAlert:
    def test_no_escalation_for_available_deadline(self, avail):
        deadline = _ind("wednesday", 14)
        now = _ind("tuesday", 10)
        assert avail.needs_escalation_alert(deadline, now) is False

    def test_escalation_when_within_alert_hours(self, avail):
        # deadline Saturday, now is Thursday just after escalation cutoff
        # alert_hours=72 means 72h before escalation_target
        # escalation_target = Thursday 18:00; now = Thursday 18:01 → within window
        deadline = _ind("saturday", 12)
        now      = _ind("thursday", 18, 1)
        assert avail.needs_escalation_alert(deadline, now) is True

    def test_escalation_when_already_past_target(self, avail):
        # deadline Saturday but now is already Friday evening (already in window)
        deadline = _ind("saturday", 12)
        now      = _ind("friday", 17)
        assert avail.needs_escalation_alert(deadline, now) is True

    def test_no_escalation_well_before_window(self, avail):
        # deadline Saturday; now is Monday morning — 4+ days away, outside 72h window
        deadline = _ind("saturday", 12)
        now      = _ind("monday", 9)
        # alert_hours=72 means 72h before Thursday 18:00 = Monday 18:00
        # Monday 09:00 is before Monday 18:00, so NOT in alert window yet
        assert avail.needs_escalation_alert(deadline, now) is False


class TestNextAvailableAfter:
    def test_available_now_returns_now(self, avail):
        mon = _ind("monday", 10)
        result = avail.next_available_after(mon)
        assert avail.is_unavailable(result) is False

    def test_saturday_returns_monday(self, avail):
        sat = _ind("saturday", 14)
        result = avail.next_available_after(sat)
        assert result.weekday() == 0   # Monday

    def test_friday_evening_returns_monday(self, avail):
        fri = _ind("friday", 18)
        result = avail.next_available_after(fri)
        assert result.weekday() == 0


# ── SLAChecker ────────────────────────────────────────────────────────────────

@pytest.fixture
def sla() -> SLAChecker:
    return SLAChecker()


class TestSLAHours:
    def test_hot_24h(self, sla):
        assert sla.sla_hours("hot") == 24

    def test_offer_ready_is_gate_zero(self, sla):
        assert sla.sla_hours("offer_ready") == 0

    def test_buyer_selected_is_gate_zero(self, sla):
        assert sla.sla_hours("buyer_selected") == 0

    def test_scope_ready_is_gate_zero(self, sla):
        assert sla.sla_hours("scope_ready") == 0

    def test_outreach_active_168h(self, sla):
        assert sla.sla_hours("outreach_active") == 168

    def test_responded_4h(self, sla):
        assert sla.sla_hours("responded") == 4

    def test_case_insensitive(self, sla):
        assert sla.sla_hours("HOT") == sla.sla_hours("hot")

    def test_unknown_status_returns_default(self, sla):
        assert sla.sla_hours("unknown_state_xyz") == 24   # _DEFAULT_SLA_HOURS


class TestComputeDeadline:
    def _now(self) -> datetime:
        return datetime(2026, 9, 28, 10, 0, tzinfo=ZoneInfo("UTC"))

    def test_hot_deadline_is_24h_after_update(self, sla):
        updated = self._now()
        expected = updated + timedelta(hours=24)
        assert sla.compute_deadline("hot", updated) == expected

    def test_gate_state_returns_none(self, sla):
        assert sla.compute_deadline("offer_ready", self._now()) is None
        assert sla.compute_deadline("buyer_selected", self._now()) is None
        assert sla.compute_deadline("scope_ready", self._now()) is None

    def test_outreach_active_deadline_7days(self, sla):
        updated = self._now()
        expected = updated + timedelta(hours=168)
        assert sla.compute_deadline("outreach_active", updated) == expected


class TestIsOverdue:
    def _utc(self, **kw) -> datetime:
        return datetime(2026, 9, 28, 12, 0, tzinfo=ZoneInfo("UTC")) + timedelta(**kw)

    def test_hot_overdue_when_25h_old(self, sla):
        updated = self._utc(hours=-25)
        now     = self._utc()
        assert sla.is_overdue("hot", updated, now) is True

    def test_hot_not_overdue_when_12h_old(self, sla):
        updated = self._utc(hours=-12)
        now     = self._utc()
        assert sla.is_overdue("hot", updated, now) is False

    def test_gate_state_never_overdue(self, sla):
        updated = self._utc(hours=-1000)
        now     = self._utc()
        assert sla.is_overdue("offer_ready", updated, now) is False

    def test_exactly_at_deadline_is_overdue(self, sla):
        updated = self._utc(hours=-24)
        now     = self._utc()
        assert sla.is_overdue("hot", updated, now) is True


class TestHoursOverdue:
    def _utc(self, **kw) -> datetime:
        return datetime(2026, 9, 28, 12, 0, tzinfo=ZoneInfo("UTC")) + timedelta(**kw)

    def test_hours_overdue_positive_when_late(self, sla):
        updated = self._utc(hours=-30)   # 30h ago; SLA=24h → 6h overdue
        now     = self._utc()
        assert sla.hours_overdue("hot", updated, now) == pytest.approx(6.0, abs=0.01)

    def test_hours_overdue_negative_when_time_remains(self, sla):
        updated = self._utc(hours=-12)   # 12h ago; 12h remain
        now     = self._utc()
        assert sla.hours_overdue("hot", updated, now) < 0

    def test_gate_state_returns_zero(self, sla):
        updated = self._utc(hours=-500)
        now     = self._utc()
        assert sla.hours_overdue("offer_ready", updated, now) == 0.0


class TestIsGateState:
    def test_offer_ready_is_gate(self, sla):
        assert sla.is_gate_state("offer_ready") is True

    def test_buyer_selected_is_gate(self, sla):
        assert sla.is_gate_state("buyer_selected") is True

    def test_appraised_is_gate(self, sla):
        assert sla.is_gate_state("appraised") is True

    def test_hot_is_not_gate(self, sla):
        assert sla.is_gate_state("hot") is False

    def test_in_dispo_is_not_gate(self, sla):
        assert sla.is_gate_state("in_dispo") is False


class TestAlertThreshold:
    def _now(self) -> datetime:
        return datetime(2026, 9, 28, 10, 0, tzinfo=ZoneInfo("UTC"))

    def test_alert_72h_before_deadline(self, sla):
        updated  = self._now()
        deadline = sla.compute_deadline("hot", updated)          # 24h from now
        alert    = sla.alert_threshold("hot", updated, 72)       # 72h before deadline
        assert alert == deadline - timedelta(hours=72)

    def test_gate_state_returns_none(self, sla):
        assert sla.alert_threshold("offer_ready", self._now(), 72) is None
