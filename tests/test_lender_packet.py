"""
Unit tests for agents/acquisition/lender_packet.py.

Coverage:
  - validate_lender_profiles: valid profile passes
  - validate_lender_profiles: missing verified_at fails
  - validate_lender_profiles: invalid date string fails
  - validate_lender_profiles: profile older than 45 days fails (fail-closed)
  - validate_lender_profiles: profile exactly 45 days old is still valid
  - validate_lender_profiles: profile 46 days old fails
  - build_lender_packet: correct loan calculation
  - build_lender_packet: respects min/max loan limits
  - recommend_lender: picks lowest cost profile
  - recommend_lender: skips stale profiles
  - recommend_lender: returns None when all profiles stale
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.acquisition.lender_packet import (
    validate_lender_profiles, build_lender_packet, recommend_lender,
    _MAX_PROFILE_AGE_DAYS,
)

_TODAY = date(2026, 9, 28)


def _profile(
    pid="p1",
    days_old=0,
    rate=0.12,
    points=0.02,
    fixed_fees=1500,
    ltc_p=0.90,
    ltc_r=1.0,
    min_loan=50000,
    max_loan=1_500_000,
) -> dict:
    verified = _TODAY - timedelta(days=days_old)
    return {
        "id":           pid,
        "verified_at":  verified.isoformat(),
        "rate":         rate,
        "points":       points,
        "fixed_fees":   fixed_fees,
        "ltc_purchase": ltc_p,
        "ltc_rehab":    ltc_r,
        "min_loan":     min_loan,
        "max_loan":     max_loan,
    }


class TestValidateLenderProfiles:
    def test_valid_profile_no_errors(self):
        assert validate_lender_profiles([_profile()], _TODAY) == []

    def test_missing_verified_at(self):
        p = _profile()
        del p["verified_at"]
        errors = validate_lender_profiles([p], _TODAY)
        assert errors
        assert "missing verified_at" in errors[0]

    def test_invalid_date_string(self):
        p = _profile()
        p["verified_at"] = "not-a-date"
        errors = validate_lender_profiles([p], _TODAY)
        assert errors
        assert "invalid verified_at" in errors[0]

    def test_exactly_max_age_ok(self):
        errors = validate_lender_profiles([_profile(days_old=_MAX_PROFILE_AGE_DAYS)], _TODAY)
        assert errors == []

    def test_one_day_over_max_age_fails(self):
        errors = validate_lender_profiles([_profile(days_old=_MAX_PROFILE_AGE_DAYS + 1)], _TODAY)
        assert errors
        assert "days old" in errors[0]

    def test_multiple_profiles_all_valid(self):
        profiles = [_profile("p1", days_old=0), _profile("p2", days_old=10)]
        assert validate_lender_profiles(profiles, _TODAY) == []

    def test_one_stale_one_valid(self):
        profiles = [_profile("p1", days_old=0), _profile("p2", days_old=50)]
        errors = validate_lender_profiles(profiles, _TODAY)
        assert len(errors) == 1
        assert "p2" in errors[0]

    def test_empty_list_no_errors(self):
        assert validate_lender_profiles([], _TODAY) == []


class TestBuildLenderPacket:
    def test_loan_calculation(self):
        p = _profile(ltc_p=0.90, ltc_r=1.0)
        deal = {"purchase_price": 100_000, "repair_estimate": 20_000, "arv": 150_000}
        packet = build_lender_packet(deal, p)
        # 100k × 0.9 + 20k × 1.0 = 110k
        assert packet["loan_requested"] == 110_000.0

    def test_respects_min_loan(self):
        p = _profile(ltc_p=0.50, min_loan=80_000)
        deal = {"purchase_price": 80_000, "repair_estimate": 5_000, "arv": 120_000}
        packet = build_lender_packet(deal, p)
        # 80k×0.5 + 5k = 45k → clipped to min_loan 80k
        assert packet["loan_requested"] == 80_000.0

    def test_respects_max_loan(self):
        p = _profile(ltc_p=0.90, ltc_r=1.0, max_loan=100_000)
        deal = {"purchase_price": 500_000, "repair_estimate": 100_000, "arv": 700_000}
        packet = build_lender_packet(deal, p)
        assert packet["loan_requested"] == 100_000.0

    def test_property_address_in_packet(self):
        deal = {"purchase_price": 100_000, "repair_estimate": 10_000, "arv": 150_000,
                "address": "123 Main St", "city": "Indianapolis"}
        packet = build_lender_packet(deal, _profile())
        assert packet["property_address"] == "123 Main St"


class TestRecommendLender:
    def test_recommends_lowest_cost(self):
        cheap  = _profile("cheap",  rate=0.10, points=0.01, fixed_fees=500)
        costly = _profile("costly", rate=0.15, points=0.03, fixed_fees=3000)
        deal   = {"purchase_price": 100_000, "repair_estimate": 20_000}
        rec    = recommend_lender([cheap, costly], deal, today=_TODAY)
        assert rec["id"] == "cheap"

    def test_skips_stale_profile(self):
        stale = _profile("stale", days_old=50, rate=0.05)
        fresh = _profile("fresh", days_old=0, rate=0.12)
        deal  = {"purchase_price": 100_000, "repair_estimate": 20_000}
        rec   = recommend_lender([stale, fresh], deal, today=_TODAY)
        assert rec["id"] == "fresh"

    def test_returns_none_when_all_stale(self):
        profiles = [_profile("p1", days_old=50), _profile("p2", days_old=60)]
        deal     = {"purchase_price": 100_000, "repair_estimate": 20_000}
        rec      = recommend_lender(profiles, deal, today=_TODAY)
        assert rec is None

    def test_empty_profiles_returns_none(self):
        assert recommend_lender([], {}, today=_TODAY) is None
