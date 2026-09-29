"""
Unit tests for agents/rehab/gate_c_queue.py — validate_change_order.

Coverage:
  - valid CO with delta > 0 requires Gate C
  - valid CO with delta <= 0 does not require Gate C
  - CO with empty reason fails validation
  - CO with duplicate co_number fails validation
  - new_total is computed correctly (budget + prior approved COs + delta)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.rehab.gate_c_queue import validate_change_order, ChangeOrderValidation


def _co(**kw) -> dict:
    return {
        "co_number":    kw.get("co_number", 1),
        "reason":       kw.get("reason", "Discovered additional framing damage"),
        "amount_delta": kw.get("amount_delta", 2_000),
    }


class TestValidateChangeOrder:
    def test_cost_increase_requires_gate_c(self):
        result = validate_change_order(_co(amount_delta=5_000), 50_000, [])
        assert result.valid is True
        assert result.requires_gate_c is True

    def test_cost_decrease_does_not_require_gate_c(self):
        result = validate_change_order(_co(amount_delta=-2_000), 50_000, [])
        assert result.valid is True
        assert result.requires_gate_c is False

    def test_zero_delta_does_not_require_gate_c(self):
        result = validate_change_order(_co(amount_delta=0), 50_000, [])
        assert result.valid is True
        assert result.requires_gate_c is False

    def test_empty_reason_fails(self):
        result = validate_change_order(_co(reason=""), 50_000, [])
        assert result.valid is False
        assert any("reason" in e for e in result.errors)

    def test_duplicate_co_number_fails(self):
        approved = [{"co_number": 1, "amount_delta": 2_000}]
        result   = validate_change_order(_co(co_number=1), 50_000, approved)
        assert result.valid is False
        assert any("already exists" in e for e in result.errors)

    def test_new_total_accounts_for_prior_cos(self):
        approved = [{"co_number": 1, "amount_delta": 5_000}]
        co = _co(co_number=2, amount_delta=3_000)
        result = validate_change_order(co, 50_000, approved)
        # new_total = 50k + 5k (prior) + 3k (this) = 58k
        assert result.valid is True

    def test_whitespace_reason_fails(self):
        result = validate_change_order(_co(reason="   "), 50_000, [])
        assert result.valid is False
