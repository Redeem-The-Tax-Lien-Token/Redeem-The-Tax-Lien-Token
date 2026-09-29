"""
Unit tests for agents/nurture/cadence.py and agents/nurture/templates.py.

All tests are pure Python — no database access.

Coverage (cadence.py):
  - cadence_due: eligible status → due when interval elapsed
  - cadence_due: ineligible status → not due
  - cadence_due: manual_override_at within 24h → not due
  - cadence_due: manual_override_at > 24h ago → due
  - cadence_due: touch_count at max → not due
  - cadence_due: touch_count below max, last_touch recent → not due
  - cadence_due: no prior touch → due immediately
  - cadence_due: cfg override for interval_days and max_touches
  - should_expire_to_dead: at and above max_touches
  - touch_number is always previous_count + 1

Coverage (templates.py):
  - get_template returns str for every bucket × touch 1-3
  - Cycle template for touch > 3
  - render_template substitutes tokens (first_name, address, city)
  - No price language in any template
  - No negotiation language in any template
  - first_name defaults to "there" when None
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.nurture.cadence import cadence_due, should_expire_to_dead, CadenceResult
from agents.nurture.templates import (
    get_template, render_template,
    WARM_BUCKET, COLD_BUCKET, NURTURE_BUCKET, OFFER_DECLINED_BUCKET,
)

_NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
_CFG = {"nurture": {"interval_days": 7, "max_touches": 26}}


def _lead(status="nurture", override_minutes_ago: int | None = None) -> dict:
    override_at = None
    if override_minutes_ago is not None:
        override_at = _NOW - timedelta(minutes=override_minutes_ago)
    return {"status": status, "manual_override_at": override_at}


# ── cadence_due ───────────────────────────────────────────────────────────────

class TestCadenceDue:
    def test_due_when_no_prior_touch(self):
        result = cadence_due(_lead(), last_touch_at=None, touch_count=0, cfg=_CFG, now=_NOW)
        assert result.due is True
        assert result.touch_number == 1

    def test_due_after_interval_elapsed(self):
        last = _NOW - timedelta(days=8)
        result = cadence_due(_lead(), last_touch_at=last, touch_count=3, cfg=_CFG, now=_NOW)
        assert result.due is True
        assert result.touch_number == 4

    def test_not_due_within_interval(self):
        last = _NOW - timedelta(days=3)
        result = cadence_due(_lead(), last_touch_at=last, touch_count=3, cfg=_CFG, now=_NOW)
        assert result.due is False
        assert result.touch_number == 0

    def test_exactly_at_interval_is_due(self):
        last = _NOW - timedelta(days=7)
        result = cadence_due(_lead(), last_touch_at=last, touch_count=1, cfg=_CFG, now=_NOW)
        assert result.due is True

    def test_ineligible_status_not_due(self):
        for status in ("hot", "under_contract", "dead", "dnc", "offer_sent"):
            result = cadence_due(_lead(status=status), None, 0, _CFG, _NOW)
            assert result.due is False, f"status={status} should not be due"

    def test_eligible_statuses_are_due(self):
        for status in ("warm", "cold", "nurture", "offer_declined"):
            result = cadence_due(_lead(status=status), None, 0, _CFG, _NOW)
            assert result.due is True, f"status={status} should be due"

    def test_manual_override_within_24h_blocks(self):
        result = cadence_due(_lead(override_minutes_ago=60), None, 0, _CFG, _NOW)
        assert result.due is False
        assert "manual_override" in result.reason

    def test_manual_override_older_than_24h_allows(self):
        result = cadence_due(_lead(override_minutes_ago=1500), None, 0, _CFG, _NOW)
        assert result.due is True

    def test_max_touches_reached_not_due(self):
        result = cadence_due(_lead(), None, 26, _CFG, _NOW)
        assert result.due is False
        assert "max_touches" in result.reason

    def test_one_below_max_is_due(self):
        result = cadence_due(_lead(), None, 25, _CFG, _NOW)
        assert result.due is True
        assert result.touch_number == 26

    def test_touch_number_is_count_plus_one(self):
        for count in (0, 5, 10, 20):
            result = cadence_due(_lead(), None, count, _CFG, _NOW)
            if result.due:
                assert result.touch_number == count + 1

    def test_cfg_override_interval(self):
        cfg = {"nurture": {"interval_days": 14, "max_touches": 26}}
        last = _NOW - timedelta(days=10)   # 10 days ago, but interval is 14
        result = cadence_due(_lead(), last, 1, cfg, _NOW)
        assert result.due is False

    def test_cfg_override_max_touches(self):
        cfg = {"nurture": {"interval_days": 7, "max_touches": 5}}
        result = cadence_due(_lead(), None, 5, cfg, _NOW)
        assert result.due is False


class TestShouldExpireToDead:
    def test_at_max_returns_true(self):
        assert should_expire_to_dead(26, _CFG) is True

    def test_above_max_returns_true(self):
        assert should_expire_to_dead(30, _CFG) is True

    def test_below_max_returns_false(self):
        assert should_expire_to_dead(25, _CFG) is False

    def test_zero_touches_returns_false(self):
        assert should_expire_to_dead(0, _CFG) is False


# ── templates ─────────────────────────────────────────────────────────────────

class TestGetTemplate:
    @pytest.mark.parametrize("bucket", [WARM_BUCKET, COLD_BUCKET, NURTURE_BUCKET, OFFER_DECLINED_BUCKET])
    @pytest.mark.parametrize("touch", [1, 2, 3])
    def test_returns_non_empty_string(self, bucket, touch):
        tmpl = get_template(bucket, touch)
        assert isinstance(tmpl, str)
        assert len(tmpl) > 10

    def test_touch_4_uses_cycle(self):
        t4 = get_template(WARM_BUCKET, 4)
        t5 = get_template(WARM_BUCKET, 5)
        assert isinstance(t4, str)
        assert isinstance(t5, str)

    def test_cycle_wraps(self):
        # Three cycle templates; touch 4, 7, 10 should be the same
        t4  = get_template(NURTURE_BUCKET, 4)
        t7  = get_template(NURTURE_BUCKET, 7)
        assert t4 == t7

    def test_different_buckets_different_templates(self):
        warm_t1    = get_template(WARM_BUCKET, 1)
        cold_t1    = get_template(COLD_BUCKET, 1)
        assert warm_t1 != cold_t1


class TestRenderTemplate:
    def test_substitutes_first_name(self):
        rendered = render_template(WARM_BUCKET, 1, "Jane", "123 Main St")
        assert "Jane" in rendered

    def test_substitutes_address(self):
        rendered = render_template(WARM_BUCKET, 1, "Jane", "999 Oak Ave")
        assert "999 Oak Ave" in rendered

    def test_first_name_defaults_to_there(self):
        rendered = render_template(WARM_BUCKET, 1, None, "123 Main St")
        assert "there" in rendered

    def test_no_price_language_in_any_template(self):
        price_keywords = ["$", "counter", "higher", "lower", "negotiate"]
        for bucket in [WARM_BUCKET, COLD_BUCKET, NURTURE_BUCKET, OFFER_DECLINED_BUCKET]:
            for touch in range(1, 8):
                rendered = render_template(bucket, touch, "Jane", "123 Main")
                for kw in price_keywords:
                    assert kw not in rendered, (
                        f"Price/negotiation keyword '{kw}' found in {bucket} touch {touch}: {rendered}"
                    )

    def test_no_broker_agent_claim(self):
        """Templates must never imply the operator is a licensed agent or broker."""
        broker_keywords = ["licensed agent", "real estate agent", "broker", "brokerage"]
        for bucket in [WARM_BUCKET, COLD_BUCKET, NURTURE_BUCKET, OFFER_DECLINED_BUCKET]:
            for touch in [1, 2, 3]:
                rendered = render_template(bucket, touch, "Jane", "123 Main")
                for kw in broker_keywords:
                    assert kw.lower() not in rendered.lower(), (
                        f"Broker/agent claim '{kw}' found in {bucket} touch {touch}"
                    )
