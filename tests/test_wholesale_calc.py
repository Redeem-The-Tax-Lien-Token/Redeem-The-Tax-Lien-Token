"""
Unit tests for core/strategy/wholesale.py, eligibility.py, and offer_policy.py.

Coverage goals:
  - compute_wholesale: every neighborhood class, MAO formula, rounding, negative MAO
  - check_wholesale: every gate individually, compound failures
  - select_offer_price: eligible/ineligible paths, unknown policy raises
  - 100% branch coverage of all three modules
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.strategy.eligibility import IneligibleReason, WholesaleEligibility, check_wholesale
from core.strategy.offer_policy import select_offer_price
from core.strategy.wholesale import WholesaleCase, compute_wholesale

# ── Minimal config fixture ─────────────────────────────────────────────────────

_CFG = {
    "wholesale": {
        "discount_rates": {"A": 0.75, "B": 0.75, "C": 0.65, "STD": 0.70},
        "assignment_fee": 10_000,
        "min_wholesale_fee": 7_500,
        "min_buyer_count": 3,
        "min_seller_timeline_days": 21,
    },
    "offer_policy": "highest_eligible",
}


# ── compute_wholesale ──────────────────────────────────────────────────────────

class TestComputeWholesale:
    def test_a_class_mao_and_fee(self):
        case = compute_wholesale(200_000, 20_000, "A", cfg=_CFG)
        assert case.discount_rate == 0.75
        assert case.mao == pytest.approx(120_000)
        assert case.offer_price == pytest.approx(120_000)
        assert case.fee_at_offer == pytest.approx(10_000)

    def test_b_class_same_rate_as_a(self):
        case = compute_wholesale(175_000, 25_000, "B", cfg=_CFG)
        assert case.discount_rate == 0.75
        assert case.mao == pytest.approx(96_250)
        assert case.offer_price == pytest.approx(96_000)   # rounded down
        assert case.fee_at_offer == pytest.approx(10_250)  # rounding adds to fee

    def test_c_class_lower_rate(self):
        case = compute_wholesale(120_000, 15_000, "C", cfg=_CFG)
        assert case.discount_rate == 0.65
        assert case.mao == pytest.approx(53_000)
        assert case.offer_price == pytest.approx(53_000)

    def test_std_class_rate(self):
        case = compute_wholesale(160_000, 30_000, "STD", cfg=_CFG)
        assert case.discount_rate == 0.70
        assert case.mao == pytest.approx(72_000)
        assert case.offer_price == pytest.approx(72_000)

    def test_case_is_lowercase_normalized(self):
        case_lower = compute_wholesale(200_000, 20_000, "a", cfg=_CFG)
        assert case_lower.neighborhood_class == "A"

    def test_unknown_class_raises(self):
        with pytest.raises(ValueError, match="Unknown neighborhood_class"):
            compute_wholesale(200_000, 20_000, "D", cfg=_CFG)

    def test_mao_negative_sets_offer_to_zero(self):
        # ARV=90k, repairs=60k, STD → MAO = 63k - 60k - 10k = -7k
        case = compute_wholesale(90_000, 60_000, "STD", cfg=_CFG)
        assert case.mao == pytest.approx(-7_000)
        assert case.offer_price == 0.0

    def test_mao_exactly_zero_sets_offer_to_zero(self):
        # ARV=100k, repairs=60k, STD → MAO = 70k - 60k - 10k = 0
        case = compute_wholesale(100_000, 60_000, "STD", cfg=_CFG)
        assert case.mao == pytest.approx(0)
        assert case.offer_price == 0.0

    def test_rounding_floors_not_rounds(self):
        # MAO = 96,250 → floor to 96,000 (not 97,000)
        case = compute_wholesale(175_000, 25_000, "B", cfg=_CFG)
        assert case.offer_price == 96_000

    def test_light_repairs_high_mao(self):
        case = compute_wholesale(250_000, 5_000, "A", cfg=_CFG)
        assert case.mao == pytest.approx(172_500)
        assert case.offer_price == pytest.approx(172_000)
        assert case.fee_at_offer == pytest.approx(10_500)

    def test_fee_at_offer_uses_offer_price_not_mao(self):
        # fee = arv*rate - repairs - offer_price (not arv*rate - repairs - mao)
        case = compute_wholesale(175_000, 25_000, "B", cfg=_CFG)
        expected_fee = 175_000 * 0.75 - 25_000 - case.offer_price
        assert case.fee_at_offer == pytest.approx(expected_fee)

    def test_very_large_arv(self):
        case = compute_wholesale(1_000_000, 100_000, "A", cfg=_CFG)
        assert case.mao > 0
        assert case.offer_price % 1_000 == 0   # always a multiple of $1k

    def test_dataclass_is_frozen(self):
        case = compute_wholesale(200_000, 20_000, "A", cfg=_CFG)
        with pytest.raises(Exception):   # FrozenInstanceError
            case.arv = 999_999  # type: ignore[misc]


# ── check_wholesale ────────────────────────────────────────────────────────────

def _eligible_case() -> WholesaleCase:
    return compute_wholesale(200_000, 20_000, "A", cfg=_CFG)


def _ineligible_case() -> WholesaleCase:
    return compute_wholesale(90_000, 60_000, "STD", cfg=_CFG)


class TestCheckWholesale:
    def test_all_gates_pass(self):
        elig = check_wholesale(_eligible_case(), 8, 45, cfg=_CFG)
        assert elig.all_pass is True
        assert elig.reasons == ()

    def test_spread_too_thin(self):
        elig = check_wholesale(_ineligible_case(), 8, 45, cfg=_CFG)
        assert elig.spread_ok is False
        assert IneligibleReason.SPREAD_TOO_THIN in elig.reasons
        assert elig.all_pass is False

    def test_mao_exactly_zero_is_ineligible(self):
        case = compute_wholesale(100_000, 60_000, "STD", cfg=_CFG)
        elig = check_wholesale(case, 8, 45, cfg=_CFG)
        assert elig.spread_ok is False
        assert IneligibleReason.SPREAD_TOO_THIN in elig.reasons

    def test_low_buyer_demand_zero_buyers(self):
        elig = check_wholesale(_eligible_case(), 0, 45, cfg=_CFG)
        assert elig.buyer_ok is False
        assert IneligibleReason.LOW_BUYER_DEMAND in elig.reasons
        assert elig.all_pass is False

    def test_low_buyer_demand_below_min(self):
        # min_buyer_count = 3; 2 buyers → fails
        elig = check_wholesale(_eligible_case(), 2, 45, cfg=_CFG)
        assert elig.buyer_ok is False

    def test_buyer_demand_exactly_at_min(self):
        elig = check_wholesale(_eligible_case(), 3, 45, cfg=_CFG)
        assert elig.buyer_ok is True

    def test_timeline_too_short(self):
        elig = check_wholesale(_eligible_case(), 8, 10, cfg=_CFG)
        assert elig.timeline_ok is False
        assert IneligibleReason.TIMELINE_TOO_SHORT in elig.reasons
        assert elig.all_pass is False

    def test_timeline_exactly_at_min(self):
        elig = check_wholesale(_eligible_case(), 8, 21, cfg=_CFG)
        assert elig.timeline_ok is True

    def test_compound_failure_spread_and_buyers(self):
        # mao=-7k so fee_at_offer=3k also triggers FEE_TOO_LOW
        case = compute_wholesale(90_000, 60_000, "STD", cfg=_CFG)
        elig = check_wholesale(case, 0, 45, cfg=_CFG)
        assert IneligibleReason.SPREAD_TOO_THIN in elig.reasons
        assert IneligibleReason.LOW_BUYER_DEMAND in elig.reasons
        assert len(elig.reasons) >= 2

    def test_compound_failure_all_four_gates(self):
        case = compute_wholesale(90_000, 60_000, "STD", cfg=_CFG)
        # negative MAO → fee_at_offer < min_wholesale_fee also
        elig = check_wholesale(case, 0, 5, cfg=_CFG)
        assert elig.all_pass is False
        assert len(elig.reasons) >= 3   # spread + buyers + timeline at minimum

    def test_fee_ok_when_eligible(self):
        elig = check_wholesale(_eligible_case(), 8, 45, cfg=_CFG)
        assert elig.fee_ok is True

    def test_reasons_are_strings_not_enums(self):
        case = compute_wholesale(90_000, 60_000, "STD", cfg=_CFG)
        elig = check_wholesale(case, 0, 5, cfg=_CFG)
        for r in elig.reasons:
            assert isinstance(r, str), f"Expected str, got {type(r)}"

    def test_eligibility_is_frozen(self):
        elig = check_wholesale(_eligible_case(), 8, 45, cfg=_CFG)
        with pytest.raises(Exception):
            elig.all_pass = False  # type: ignore[misc]


# ── select_offer_price ─────────────────────────────────────────────────────────

class TestSelectOfferPrice:
    def _run(self, buyers=8, timeline=45, cfg=_CFG):
        case = compute_wholesale(200_000, 20_000, "A", cfg=cfg)
        elig = check_wholesale(case, buyers, timeline, cfg=cfg)
        return select_offer_price(case, elig, cfg=cfg), case, elig

    def test_returns_offer_price_when_eligible(self):
        price, case, _ = self._run()
        assert price == pytest.approx(case.offer_price)

    def test_returns_none_when_ineligible(self):
        price, _, _ = self._run(buyers=0)
        assert price is None

    def test_returns_none_when_timeline_fails(self):
        price, _, _ = self._run(timeline=5)
        assert price is None

    def test_offer_price_is_multiple_of_1000(self):
        price, _, _ = self._run()
        assert price % 1_000 == 0

    def test_highest_value_policy_also_works(self):
        cfg2 = {**_CFG, "offer_policy": "highest_value"}
        price, case, _ = self._run(cfg=cfg2)
        assert price == pytest.approx(case.offer_price)

    def test_unknown_policy_raises(self):
        cfg2 = {**_CFG, "offer_policy": "auction"}
        case = compute_wholesale(200_000, 20_000, "A", cfg=_CFG)
        elig = check_wholesale(case, 8, 45, cfg=_CFG)
        with pytest.raises(ValueError, match="Unknown offer_policy"):
            select_offer_price(case, elig, cfg=cfg2)

    def test_tight_spread_boundary_eligible(self):
        # MAO = $1k → should be eligible and offer exactly $1k
        case = compute_wholesale(100_000, 59_000, "STD", cfg=_CFG)
        elig = check_wholesale(case, 3, 21, cfg=_CFG)
        price = select_offer_price(case, elig, cfg=_CFG)
        assert price == pytest.approx(1_000)


# ── WholesaleCase field contract ───────────────────────────────────────────────

class TestWholesaleCaseFields:
    def test_all_fields_present(self):
        case = compute_wholesale(200_000, 20_000, "A", cfg=_CFG)
        assert hasattr(case, "arv")
        assert hasattr(case, "repairs")
        assert hasattr(case, "neighborhood_class")
        assert hasattr(case, "assignment_fee")
        assert hasattr(case, "discount_rate")
        assert hasattr(case, "mao")
        assert hasattr(case, "offer_price")
        assert hasattr(case, "fee_at_offer")

    def test_assignment_fee_comes_from_config(self):
        cfg2 = {**_CFG, "wholesale": {**_CFG["wholesale"], "assignment_fee": 15_000}}
        case = compute_wholesale(200_000, 20_000, "A", cfg=cfg2)
        assert case.assignment_fee == pytest.approx(15_000)
        assert case.mao == pytest.approx(200_000 * 0.75 - 20_000 - 15_000)
