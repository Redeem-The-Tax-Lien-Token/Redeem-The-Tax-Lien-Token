"""
Unit tests for agents/dispo_coordinator/buyer_qualifier.py.

All tests are pure Python — no database access.

Coverage:
  - qualify_buyer: all pass → qualified=True
  - qualify_buyer: POF not confirmed → DISQUALIFIED
  - qualify_buyer: timeline too long → DISQUALIFIED
  - qualify_buyer: EMD insufficient → DISQUALIFIED
  - qualify_buyer: offer_amount zero or missing → DISQUALIFIED
  - qualify_buyer: multiple failures → multiple reasons
  - qualify_buyer: with cfg override for closing_window_days
  - rank_offers: highest offer first, EMD as tie-breaker, timeline as secondary
  - rank_offers: single offer
  - rank_offers: empty list
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.dispo_coordinator.buyer_qualifier import qualify_buyer, rank_offers


_DEAL = {"emd_amount": 1000.0, "offer_amount": 55000.0}
_CFG  = {
    "contract": {
        "closing_window_days": {"wholesale": 30},
        "emd_amount": 1000,
    }
}


def _offer(**kwargs):
    base = {
        "offer_amount":  65000.0,
        "pof_confirmed": True,
        "timeline_days": 21,
        "emd_capacity":  1500.0,
        "buyer_id":      1,
    }
    base.update(kwargs)
    return base


# ── qualify_buyer ─────────────────────────────────────────────────────────────

class TestQualifyBuyer:
    def test_all_pass_returns_qualified(self):
        result = qualify_buyer(_offer(), _DEAL, _CFG)
        assert result.qualified is True
        assert result.reasons == ()

    def test_pof_not_confirmed_disqualifies(self):
        result = qualify_buyer(_offer(pof_confirmed=False), _DEAL, _CFG)
        assert result.qualified is False
        assert any("POF" in r for r in result.reasons)

    def test_timeline_too_long_disqualifies(self):
        result = qualify_buyer(_offer(timeline_days=45), _DEAL, _CFG)
        assert result.qualified is False
        assert any("TIMELINE" in r for r in result.reasons)

    def test_timeline_at_limit_is_ok(self):
        result = qualify_buyer(_offer(timeline_days=30), _DEAL, _CFG)
        assert result.qualified is True

    def test_timeline_one_over_limit_disqualifies(self):
        result = qualify_buyer(_offer(timeline_days=31), _DEAL, _CFG)
        assert result.qualified is False

    def test_emd_insufficient_disqualifies(self):
        result = qualify_buyer(_offer(emd_capacity=500.0), _DEAL, _CFG)
        assert result.qualified is False
        assert any("EMD" in r for r in result.reasons)

    def test_emd_exactly_at_requirement_passes(self):
        result = qualify_buyer(_offer(emd_capacity=1000.0), _DEAL, _CFG)
        assert result.qualified is True

    def test_zero_offer_amount_disqualifies(self):
        result = qualify_buyer(_offer(offer_amount=0), _DEAL, _CFG)
        assert result.qualified is False

    def test_none_offer_amount_disqualifies(self):
        result = qualify_buyer(_offer(offer_amount=None), _DEAL, _CFG)
        assert result.qualified is False

    def test_multiple_failures_produce_multiple_reasons(self):
        result = qualify_buyer(
            _offer(pof_confirmed=False, timeline_days=60, emd_capacity=100.0),
            _DEAL, _CFG,
        )
        assert result.qualified is False
        assert len(result.reasons) >= 3

    def test_no_cfg_uses_defaults(self):
        # Should not raise; defaults are permissive enough for a sane offer
        result = qualify_buyer(_offer(), _DEAL, cfg=None)
        assert isinstance(result.qualified, bool)

    def test_cfg_override_closing_window(self):
        cfg_tight = {"contract": {"closing_window_days": {"wholesale": 10}, "emd_amount": 1000}}
        # 21-day timeline fails a 10-day window
        result = qualify_buyer(_offer(timeline_days=11), _DEAL, cfg_tight)
        assert result.qualified is False

    def test_pof_required_flag(self, monkeypatch):
        import agents.dispo_coordinator.buyer_qualifier as bq
        monkeypatch.setattr(bq, "_REQUIRE_POF", False)
        result = qualify_buyer(_offer(pof_confirmed=False), _DEAL, _CFG)
        # POF gate is skipped; other checks pass
        assert result.qualified is True


# ── rank_offers ───────────────────────────────────────────────────────────────

class TestRankOffers:
    def _make_qualified(self, buyer_id, offer_amount, emd, timeline=21):
        return {
            "buyer_id":     buyer_id,
            "offer_amount": offer_amount,
            "emd_capacity": emd,
            "timeline_days": timeline,
            "qualified":    True,
        }

    def test_highest_offer_ranked_first(self):
        offers = [
            self._make_qualified(1, 60_000, 2000),
            self._make_qualified(2, 70_000, 2000),
            self._make_qualified(3, 65_000, 2000),
        ]
        ranked = rank_offers(offers, _DEAL)
        assert ranked[0]["buyer_id"] == 2
        assert ranked[0]["ranked"] == 1

    def test_emd_tiebreaker(self):
        offers = [
            self._make_qualified(1, 65_000, 1000),
            self._make_qualified(2, 65_000, 3000),
        ]
        ranked = rank_offers(offers, _DEAL)
        assert ranked[0]["buyer_id"] == 2   # higher EMD wins tie

    def test_timeline_tiebreaker(self):
        offers = [
            self._make_qualified(1, 65_000, 2000, timeline=30),
            self._make_qualified(2, 65_000, 2000, timeline=14),
        ]
        ranked = rank_offers(offers, _DEAL)
        assert ranked[0]["buyer_id"] == 2   # shorter timeline wins tie

    def test_ranked_field_assigned(self):
        offers = [self._make_qualified(1, 60_000, 1000), self._make_qualified(2, 70_000, 1000)]
        ranked = rank_offers(offers, _DEAL)
        assert ranked[0]["ranked"] == 1
        assert ranked[1]["ranked"] == 2

    def test_single_offer_ranked_one(self):
        offers = [self._make_qualified(1, 65_000, 2000)]
        ranked = rank_offers(offers, _DEAL)
        assert len(ranked) == 1
        assert ranked[0]["ranked"] == 1

    def test_empty_list_returns_empty(self):
        assert rank_offers([], _DEAL) == []
