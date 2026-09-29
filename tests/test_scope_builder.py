"""
Unit tests for agents/rehab/scope_builder.py.

Coverage:
  - build_scope_from_estimate: correct tier weights applied
  - build_scope_from_estimate: total budget matches estimate (within rounding)
  - build_scope_from_estimate: all tiers produce non-empty scopes
  - total_scope_budget: sums correctly
  - validate_scope: empty list is an error
  - validate_scope: missing category/description is an error
  - validate_scope: negative total_cost is an error
  - validate_scope: zero total is an error
  - validate_scope: valid scope has no errors
  - ScopeItem.to_dict: round-trips correctly
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.rehab.scope_builder import (
    ScopeItem, build_scope_from_estimate, total_scope_budget, validate_scope,
    STANDARD_CATEGORIES,
)


class TestBuildScopeFromEstimate:
    @pytest.mark.parametrize("tier", ["LIGHT", "MEDIUM", "HEAVY", "GUT"])
    def test_all_tiers_produce_items(self, tier):
        items = build_scope_from_estimate(50_000, tier)
        assert len(items) > 0

    @pytest.mark.parametrize("tier", ["LIGHT", "MEDIUM", "HEAVY", "GUT"])
    def test_total_roughly_matches_estimate(self, tier):
        estimate = 60_000.0
        items    = build_scope_from_estimate(estimate, tier)
        total    = total_scope_budget(items)
        # Weights should sum to 1.0 (or very close)
        assert abs(total - estimate) < 1.0, f"tier={tier}: total={total} estimate={estimate}"

    def test_all_items_have_positive_cost(self):
        items = build_scope_from_estimate(40_000, "MEDIUM")
        assert all(i.total_cost > 0 for i in items)

    def test_all_items_have_category(self):
        items = build_scope_from_estimate(40_000, "HEAVY")
        assert all(i.category for i in items)

    def test_sort_order_assigned(self):
        items = build_scope_from_estimate(40_000, "LIGHT")
        orders = [i.sort_order for i in items]
        assert sorted(orders) == orders  # ascending


class TestTotalScopeBudget:
    def test_sums_correctly(self):
        items = [
            ScopeItem(category="roof", description="New roof", total_cost=10_000),
            ScopeItem(category="hvac", description="HVAC", total_cost=5_000),
        ]
        assert total_scope_budget(items) == 15_000.0

    def test_empty_list_is_zero(self):
        assert total_scope_budget([]) == 0.0

    def test_rounding(self):
        items = [ScopeItem(category="a", description="a", total_cost=0.1),
                 ScopeItem(category="b", description="b", total_cost=0.2)]
        assert total_scope_budget(items) == 0.3


class TestValidateScope:
    def _item(self, **kw) -> ScopeItem:
        return ScopeItem(
            category=kw.get("category", "roof"),
            description=kw.get("description", "New roof"),
            total_cost=kw.get("total_cost", 10_000),
        )

    def test_valid_scope_no_errors(self):
        items = [self._item(), self._item(category="hvac", description="HVAC")]
        assert validate_scope(items) == []

    def test_empty_list_is_error(self):
        errors = validate_scope([])
        assert errors
        assert "no line items" in errors[0]

    def test_missing_category_is_error(self):
        item = ScopeItem(category="", description="desc", total_cost=1_000)
        errors = validate_scope([item])
        assert any("category" in e for e in errors)

    def test_missing_description_is_error(self):
        item = ScopeItem(category="roof", description="", total_cost=1_000)
        errors = validate_scope([item])
        assert any("description" in e for e in errors)

    def test_negative_cost_is_error(self):
        item = ScopeItem(category="roof", description="roof", total_cost=-500)
        errors = validate_scope([item])
        assert any("negative" in e for e in errors)

    def test_zero_total_is_error(self):
        item = ScopeItem(category="roof", description="roof", total_cost=0)
        errors = validate_scope([item])
        assert any("Total scope budget" in e for e in errors)


class TestScopeItemToDict:
    def test_round_trip(self):
        item = ScopeItem(
            category="flooring", description="LVP flooring",
            total_cost=8_000, quantity=1200, unit="sqft", unit_cost=6.67, sort_order=3,
        )
        d = item.to_dict()
        assert d["category"]    == "flooring"
        assert d["total_cost"]  == 8_000
        assert d["quantity"]    == 1200
        assert d["unit"]        == "sqft"
        assert d["sort_order"]  == 3
