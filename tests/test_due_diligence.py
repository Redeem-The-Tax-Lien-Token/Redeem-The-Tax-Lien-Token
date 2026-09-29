"""
Unit tests for agents/acquisition/due_diligence.py.

Coverage:
  - initial_dd_rows: correct check names, all start as pending
  - evaluate_dd_status: all_pass, has_fail, pending cases
  - can_advance_to_funding: only when all pass or waived
  - can_advance_to_funding: blocks on any fail or pending
  - can_advance_to_funding: empty list returns False
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.acquisition.due_diligence import (
    DD_CHECKS, initial_dd_rows, evaluate_dd_status, can_advance_to_funding,
    WAIVABLE_CHECKS,
)


class TestInitialDdRows:
    def test_correct_check_names(self):
        rows = initial_dd_rows(42)
        names = [r["check_name"] for r in rows]
        for check in DD_CHECKS:
            assert check in names

    def test_all_pending(self):
        rows = initial_dd_rows(1)
        assert all(r["status"] == "pending" for r in rows)

    def test_correct_deal_id(self):
        rows = initial_dd_rows(99)
        assert all(r["deal_id"] == 99 for r in rows)

    def test_count_matches_dd_checks(self):
        rows = initial_dd_rows(1)
        assert len(rows) == len(DD_CHECKS)


class TestEvaluateDdStatus:
    def _rows(self, statuses: dict[str, str]) -> list[dict]:
        return [
            {"check_name": name, "status": statuses.get(name, "pending")}
            for name in DD_CHECKS
        ]

    def test_all_pass(self):
        rows = [{"check_name": n, "status": "pass"} for n in DD_CHECKS]
        result = evaluate_dd_status(rows)
        assert result.outcome == "all_pass"
        assert not result.failed_checks
        assert not result.pending_checks

    def test_all_waived(self):
        rows = [{"check_name": n, "status": "waived"} for n in DD_CHECKS]
        result = evaluate_dd_status(rows)
        assert result.outcome == "all_pass"

    def test_mixed_pass_and_waived(self):
        rows = []
        for i, name in enumerate(DD_CHECKS):
            rows.append({"check_name": name, "status": "waived" if i % 2 == 0 else "pass"})
        result = evaluate_dd_status(rows)
        assert result.outcome == "all_pass"

    def test_has_fail(self):
        rows = [{"check_name": n, "status": "pass"} for n in DD_CHECKS]
        rows[0]["status"] = "fail"
        result = evaluate_dd_status(rows)
        assert result.outcome == "has_fail"
        assert DD_CHECKS[0] in result.failed_checks

    def test_has_pending(self):
        rows = [{"check_name": n, "status": "pass"} for n in DD_CHECKS]
        rows[1]["status"] = "pending"
        result = evaluate_dd_status(rows)
        assert result.outcome == "pending"
        assert DD_CHECKS[1] in result.pending_checks

    def test_fail_takes_precedence_over_pending(self):
        rows = [{"check_name": n, "status": "pass"} for n in DD_CHECKS]
        rows[0]["status"] = "fail"
        rows[1]["status"] = "pending"
        result = evaluate_dd_status(rows)
        assert result.outcome == "has_fail"

    def test_multiple_fails(self):
        rows = [{"check_name": n, "status": "fail"} for n in DD_CHECKS]
        result = evaluate_dd_status(rows)
        assert len(result.failed_checks) == len(DD_CHECKS)

    def test_empty_rows(self):
        result = evaluate_dd_status([])
        assert result.outcome == "all_pass"  # vacuously true — no fails/pending


class TestCanAdvanceToFunding:
    def test_all_pass_can_advance(self):
        rows = [{"check_name": n, "status": "pass"} for n in DD_CHECKS]
        assert can_advance_to_funding(rows) is True

    def test_all_waived_can_advance(self):
        rows = [{"check_name": n, "status": "waived"} for n in DD_CHECKS]
        assert can_advance_to_funding(rows) is True

    def test_one_fail_blocks(self):
        rows = [{"check_name": n, "status": "pass"} for n in DD_CHECKS]
        rows[0]["status"] = "fail"
        assert can_advance_to_funding(rows) is False

    def test_one_pending_blocks(self):
        rows = [{"check_name": n, "status": "pass"} for n in DD_CHECKS]
        rows[-1]["status"] = "pending"
        assert can_advance_to_funding(rows) is False

    def test_empty_list_does_not_advance(self):
        assert can_advance_to_funding([]) is False

    def test_pass_and_waived_mix_advances(self):
        rows = []
        for i, name in enumerate(DD_CHECKS):
            rows.append({"check_name": name, "status": "waived" if i % 3 == 0 else "pass"})
        assert can_advance_to_funding(rows) is True
