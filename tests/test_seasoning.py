"""
Unit tests for agents/refinance/seasoning.py.

Coverage:
  - months_between: same month = 0
  - months_between: exactly 6 months
  - months_between: partial month rounds down
  - check_seasoning: < min_seasoning → ineligible
  - check_seasoning: ≥ min but < full with cost_basis_value rule → eligible, cost_basis
  - check_seasoning: ≥ full_value_seasoning → eligible, full_value
  - check_seasoning: ≥ min but < full with no early_rule → ineligible
  - check_seasoning: months_owned reported correctly
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.refinance.seasoning import check_seasoning, months_between


_LENDER = {
    "id":                          "dscr_default",
    "min_seasoning_months":        3,
    "full_value_seasoning_months": 6,
    "early_rule":                  "cost_basis_value",
}


class TestMonthsBetween:
    def test_same_day_zero(self):
        d = date(2026, 1, 15)
        assert months_between(d, d) == 0

    def test_exactly_one_month(self):
        assert months_between(date(2026, 1, 15), date(2026, 2, 15)) == 1

    def test_exactly_six_months(self):
        assert months_between(date(2026, 3, 1), date(2026, 9, 1)) == 6

    def test_partial_month_rounds_down(self):
        # 2026-03-20 → 2026-09-15: end day (15) < start day (20) → 5 months
        assert months_between(date(2026, 3, 20), date(2026, 9, 15)) == 5

    def test_end_day_equal_start_day_counts(self):
        # 2026-03-20 → 2026-09-20: exactly 6 months
        assert months_between(date(2026, 3, 20), date(2026, 9, 20)) == 6

    def test_cross_year(self):
        assert months_between(date(2025, 10, 1), date(2026, 4, 1)) == 6

    def test_end_before_start_returns_zero(self):
        assert months_between(date(2026, 9, 1), date(2026, 3, 1)) == 0


class TestCheckSeasoning:
    def test_below_min_seasoning_ineligible(self):
        acq_date = date(2026, 7, 1)
        today    = date(2026, 9, 1)   # 2 months
        result   = check_seasoning(acq_date, _LENDER, now=today)
        assert result.eligible is False
        assert result.value_basis_rule == "ineligible"
        assert result.months_owned == 2

    def test_at_min_with_early_rule_eligible(self):
        acq_date = date(2026, 6, 1)
        today    = date(2026, 9, 1)   # 3 months = min
        result   = check_seasoning(acq_date, _LENDER, now=today)
        assert result.eligible is True
        assert result.value_basis_rule == "cost_basis_value"
        assert result.months_owned == 3

    def test_between_min_and_full_uses_cost_basis(self):
        acq_date = date(2026, 5, 1)
        today    = date(2026, 9, 1)   # 4 months: > 3 min, < 6 full
        result   = check_seasoning(acq_date, _LENDER, now=today)
        assert result.eligible is True
        assert result.value_basis_rule == "cost_basis_value"

    def test_at_full_seasoning_uses_full_value(self):
        acq_date = date(2026, 3, 1)
        today    = date(2026, 9, 1)   # exactly 6 months
        result   = check_seasoning(acq_date, _LENDER, now=today)
        assert result.eligible is True
        assert result.value_basis_rule == "full_value"

    def test_above_full_seasoning_uses_full_value(self):
        acq_date = date(2025, 9, 1)
        today    = date(2026, 9, 1)   # 12 months
        result   = check_seasoning(acq_date, _LENDER, now=today)
        assert result.eligible is True
        assert result.value_basis_rule == "full_value"

    def test_no_early_rule_below_full_ineligible(self):
        lender_no_early = {
            "id":                          "strict",
            "min_seasoning_months":        3,
            "full_value_seasoning_months": 6,
            # no early_rule
        }
        acq_date = date(2026, 6, 1)
        today    = date(2026, 9, 1)   # 3 months — no early rule → ineligible
        result   = check_seasoning(acq_date, lender_no_early, now=today)
        assert result.eligible is False
        assert result.value_basis_rule == "ineligible"

    def test_months_owned_reported(self):
        acq_date = date(2026, 3, 1)
        today    = date(2026, 9, 1)
        result   = check_seasoning(acq_date, _LENDER, now=today)
        assert result.months_owned == 6
