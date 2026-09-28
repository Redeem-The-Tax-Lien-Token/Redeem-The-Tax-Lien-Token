"""
Unit tests for agents/rehab/contractor.py and agents/rehab/draws.py.

Coverage (contractor.py):
  - qualify_contractor: active + license + insurance → qualified
  - qualify_contractor: inactive → not qualified
  - qualify_contractor: no license → not qualified
  - qualify_contractor: no insurance → not qualified
  - qualify_contractor: expired insurance → not qualified
  - qualify_contractor: probation → not qualified (reasons listed)
  - rank_bids: lower bid ranked first
  - rank_bids: bids over budget ranked last
  - rank_bids: preferred tier ranked above approved at same price

Coverage (draws.py):
  - validate_draw_request: valid draw passes
  - validate_draw_request: amount <= 0 fails
  - validate_draw_request: missing description fails
  - validate_draw_request: duplicate draw_number fails
  - validate_draw_request: draw that exceeds budget fails
  - total_drawn: sums all draw amounts
  - remaining_budget: correct remainder
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.rehab.contractor import qualify_contractor, rank_bids, ContractorQualResult
from agents.rehab.draws import validate_draw_request, total_drawn, remaining_budget

_TODAY = date(2026, 9, 28)


def _contractor(**kw) -> dict:
    return {
        "active":             kw.get("active", True),
        "license_verified":   kw.get("license_verified", True),
        "insurance_verified": kw.get("insurance_verified", True),
        "insurance_expiry":   kw.get("insurance_expiry", "2027-06-01"),
        "tier_rating":        kw.get("tier_rating", "approved"),
    }


# ── qualify_contractor ────────────────────────────────────────────────────────

class TestQualifyContractor:
    def test_fully_qualified(self):
        result = qualify_contractor(_contractor(), today=_TODAY)
        assert result.qualified is True
        assert result.reasons == []

    def test_inactive_not_qualified(self):
        result = qualify_contractor(_contractor(active=False), today=_TODAY)
        assert result.qualified is False
        assert any("inactive" in r for r in result.reasons)

    def test_no_license_not_qualified(self):
        result = qualify_contractor(_contractor(license_verified=False), today=_TODAY)
        assert result.qualified is False
        assert any("License" in r for r in result.reasons)

    def test_no_insurance_not_qualified(self):
        result = qualify_contractor(_contractor(insurance_verified=False), today=_TODAY)
        assert result.qualified is False
        assert any("Insurance" in r for r in result.reasons)

    def test_expired_insurance_not_qualified(self):
        expired = (_TODAY - timedelta(days=1)).isoformat()
        result  = qualify_contractor(_contractor(insurance_expiry=expired), today=_TODAY)
        assert result.qualified is False
        assert any("expired" in r.lower() for r in result.reasons)

    def test_expiry_today_still_valid(self):
        result = qualify_contractor(_contractor(insurance_expiry=_TODAY.isoformat()), today=_TODAY)
        assert result.qualified is True

    def test_probation_not_qualified(self):
        result = qualify_contractor(_contractor(tier_rating="probation"), today=_TODAY)
        assert result.qualified is False
        assert any("probation" in r for r in result.reasons)

    def test_preferred_tier_is_qualified(self):
        result = qualify_contractor(_contractor(tier_rating="preferred"), today=_TODAY)
        assert result.qualified is True

    def test_multiple_disqualifiers_all_listed(self):
        c = _contractor(active=False, license_verified=False, insurance_verified=False)
        result = qualify_contractor(c, today=_TODAY)
        assert result.qualified is False
        assert len(result.reasons) >= 2


# ── rank_bids ─────────────────────────────────────────────────────────────────

class TestRankBids:
    def _bid(self, **kw) -> dict:
        return {
            "contractor_id": kw.get("contractor_id", 1),
            "bid_amount":    kw.get("bid_amount", 50_000),
            "timeline_days": kw.get("timeline_days", 60),
            "tier_rating":   kw.get("tier_rating", "approved"),
        }

    def test_lower_bid_ranked_first(self):
        bids   = [self._bid(bid_amount=60_000), self._bid(bid_amount=50_000)]
        ranked = rank_bids(bids, deal_budget=70_000)
        assert ranked[0]["bid_amount"] == 50_000
        assert ranked[0]["rank"] == 1

    def test_over_budget_ranked_last(self):
        bids = [
            self._bid(bid_amount=80_000),   # over budget
            self._bid(bid_amount=50_000),   # under budget
        ]
        ranked = rank_bids(bids, deal_budget=70_000)
        assert ranked[0]["bid_amount"] == 50_000
        assert ranked[-1]["bid_amount"] == 80_000
        assert ranked[-1]["over_budget"] is True

    def test_preferred_beats_approved_at_same_price(self):
        bids = [
            self._bid(tier_rating="approved",  bid_amount=50_000),
            self._bid(tier_rating="preferred", bid_amount=50_000),
        ]
        ranked = rank_bids(bids, deal_budget=70_000)
        assert ranked[0]["tier_rating"] == "preferred"

    def test_faster_timeline_breaks_tie(self):
        bids = [
            self._bid(bid_amount=50_000, timeline_days=90),
            self._bid(bid_amount=50_000, timeline_days=45),
        ]
        ranked = rank_bids(bids, deal_budget=70_000)
        assert ranked[0]["timeline_days"] == 45

    def test_rank_numbers_are_sequential(self):
        bids   = [self._bid(bid_amount=v) for v in (55_000, 60_000, 50_000)]
        ranked = rank_bids(bids, deal_budget=70_000)
        assert [r["rank"] for r in ranked] == [1, 2, 3]

    def test_empty_bids(self):
        assert rank_bids([], 50_000) == []


# ── validate_draw_request ─────────────────────────────────────────────────────

class TestValidateDrawRequest:
    def _draw(self, **kw) -> dict:
        return {
            "draw_number":       kw.get("draw_number", 1),
            "amount_requested":  kw.get("amount_requested", 5_000),
            "description":       kw.get("description", "Framing complete"),
        }

    def test_valid_draw_passes(self):
        result = validate_draw_request(self._draw(), deal_budget=50_000, draws_so_far=[])
        assert result.valid is True
        assert result.errors == []

    def test_zero_amount_fails(self):
        result = validate_draw_request(self._draw(amount_requested=0), 50_000, [])
        assert result.valid is False
        assert any("amount_requested" in e for e in result.errors)

    def test_negative_amount_fails(self):
        result = validate_draw_request(self._draw(amount_requested=-100), 50_000, [])
        assert result.valid is False

    def test_missing_description_fails(self):
        result = validate_draw_request(self._draw(description=""), 50_000, [])
        assert result.valid is False
        assert any("description" in e for e in result.errors)

    def test_duplicate_draw_number_fails(self):
        existing = [{"draw_number": 1, "amount_requested": 5_000}]
        result   = validate_draw_request(self._draw(draw_number=1), 50_000, existing)
        assert result.valid is False
        assert any("already exists" in e for e in result.errors)

    def test_exceeds_budget_fails(self):
        existing = [{"draw_number": 1, "amount_requested": 45_000}]
        result   = validate_draw_request(self._draw(draw_number=2, amount_requested=10_000),
                                         50_000, existing)
        assert result.valid is False
        assert any("exceed" in e for e in result.errors)

    def test_exactly_at_budget_passes(self):
        existing = [{"draw_number": 1, "amount_requested": 40_000}]
        result   = validate_draw_request(
            self._draw(draw_number=2, amount_requested=10_000), 50_000, existing
        )
        assert result.valid is True

    def test_zero_budget_no_validation(self):
        result = validate_draw_request(self._draw(), deal_budget=0, draws_so_far=[])
        assert result.valid is True  # 0 budget → no budget check applied


class TestDrawTotals:
    def test_total_drawn_sums_all(self):
        draws = [
            {"amount_requested": 10_000},
            {"amount_requested": 5_000},
            {"amount_requested": 3_000},
        ]
        assert total_drawn(draws) == 18_000.0

    def test_total_drawn_empty(self):
        assert total_drawn([]) == 0.0

    def test_remaining_budget(self):
        draws = [{"amount_requested": 20_000}]
        assert remaining_budget(50_000, draws) == 30_000.0

    def test_remaining_can_be_negative(self):
        draws = [{"amount_requested": 60_000}]
        assert remaining_budget(50_000, draws) == -10_000.0
