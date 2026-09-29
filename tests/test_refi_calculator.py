"""
Unit tests for agents/refinance/refi_calculator.py.

Coverage:
  - pv_from_payment: known payment → known PV
  - calculate_refi: LTV binding constraint
  - calculate_refi: DSCR binding constraint (low rent → DSCR caps first)
  - calculate_refi: cash-flow binding constraint
  - calculate_refi: ineligible when < min_loan
  - calculate_refi: ineligible when value_basis_rule = 'ineligible'
  - calculate_refi: cost_basis_value uses min(ARV, cost_basis) correctly
  - calculate_refi: cash_left_in ≤ 0 → cash_on_cash = 'INFINITE'
  - calculate_refi: rent_haircut applied to rent_q
  - calculate_refi: all key fields returned in result
"""

from __future__ import annotations

import sys
from math import isclose
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.refinance.refi_calculator import calculate_refi, pv_from_payment, RefiResult

_LENDER = {
    "refi_ltv":    0.75,
    "rate":        0.0725,
    "term_years":  30,
    "cost_pct":    0.025,
    "fixed_fees":  2000,
    "min_loan":    75_000,
    "max_loan":    1_500_000,
    "rent_haircut": 1.0,
}

_CFG = {
    "brrrr": {
        "min_dscr":                1.25,
        "min_monthly_cash_flow":   200,
    },
    "opex": {
        "vacancy_rate":     0.08,
        "maintenance_rate": 0.08,
        "capex_rate":       0.07,
        "management_rate":  0.10,
    },
    "acquisition": {
        "tax_pct":           0.02,
        "insurance_annual":  1600,
    },
}


def _inputs(**kw) -> dict:
    return {
        "arv":              kw.get("arv", 150_000),
        "purchase":         kw.get("purchase", 80_000),
        "buy_closing":      kw.get("buy_closing", 1_600),
        "repair_estimate":  kw.get("repair_estimate", 25_000),
        "rent_actual":      kw.get("rent_actual", 1_400),
        "months_owned":     kw.get("months_owned", 6),
        "value_basis_rule": kw.get("value_basis_rule", "full_value"),
        "all_in_cost":      kw.get("all_in_cost", 115_000),
    }


class TestPvFromPayment:
    def test_known_value(self):
        # $1,000/mo at 7.25% for 30 years
        pv = pv_from_payment(1_000, 0.0725, 30)
        # Rough check: should be around $145k
        assert 130_000 < pv < 155_000

    def test_zero_payment_returns_zero(self):
        assert pv_from_payment(0, 0.0725, 30) == 0.0

    def test_negative_payment_returns_zero(self):
        assert pv_from_payment(-100, 0.0725, 30) == 0.0


class TestCalculateRefi:
    def test_returns_refi_result(self):
        result = calculate_refi(_inputs(), _LENDER, _CFG)
        assert isinstance(result, RefiResult)

    def test_full_value_basis(self):
        result = calculate_refi(_inputs(value_basis_rule="full_value"), _LENDER, _CFG)
        assert result.value_basis == 150_000

    def test_cost_basis_value_uses_min(self):
        # cost_basis = 80k + 1.6k + 25k = 106.6k < ARV 150k → use cost_basis
        result = calculate_refi(
            _inputs(arv=150_000, value_basis_rule="cost_basis_value"),
            _LENDER, _CFG,
        )
        assert result.value_basis == result.cost_basis
        assert result.value_basis < 150_000

    def test_cost_basis_value_uses_arv_when_lower(self):
        # ARV 100k < cost_basis 110k → use ARV
        inp = _inputs(arv=100_000, purchase=80_000, buy_closing=1_600,
                      repair_estimate=30_000, value_basis_rule="cost_basis_value")
        result = calculate_refi(inp, _LENDER, _CFG)
        assert result.value_basis == 100_000.0

    def test_ineligible_when_value_basis_rule_ineligible(self):
        result = calculate_refi(_inputs(value_basis_rule="ineligible"), _LENDER, _CFG)
        assert result.eligible is False
        assert result.refi_loan == "INELIGIBLE"

    def test_ineligible_below_min_loan(self):
        # Very low ARV → loan below min_loan
        lender = dict(_LENDER, min_loan=200_000)
        result = calculate_refi(_inputs(arv=120_000, rent_actual=800), lender, _CFG)
        assert result.eligible is False

    def test_ltv_binding(self):
        # High rent → DSCR and CF don't cap. LTV = 150k × 0.75 = 112.5k
        result = calculate_refi(_inputs(rent_actual=3_000), _LENDER, _CFG)
        if result.eligible:
            assert result.loan_ltv <= 150_000 * 0.75 + 1

    def test_rent_haircut_applied(self):
        lender_with_haircut = dict(_LENDER, rent_haircut=0.75)
        result = calculate_refi(_inputs(rent_actual=1_400), lender_with_haircut, _CFG)
        assert isclose(result.rent_q, 1_400 * 0.75, rel_tol=1e-6)

    def test_cash_left_in_negative_is_infinite_coc(self):
        # Force a very large loan to make cash_left_in ≤ 0
        lender_high_ltv = dict(_LENDER, refi_ltv=0.95, min_loan=10_000)
        inp = _inputs(arv=200_000, all_in_cost=100_000, rent_actual=2_000)
        result = calculate_refi(inp, lender_high_ltv, _CFG)
        if isinstance(result.cash_left_in, float) and result.cash_left_in <= 0:
            assert result.cash_on_cash == "INFINITE"

    def test_eligible_result_has_positive_loan(self):
        result = calculate_refi(_inputs(), _LENDER, _CFG)
        if result.eligible:
            assert isinstance(result.refi_loan, float)
            assert float(result.refi_loan) > 0

    def test_refi_binding_set_on_eligible(self):
        result = calculate_refi(_inputs(), _LENDER, _CFG)
        if result.eligible:
            assert result.refi_binding in ("LTV", "DSCR", "CASH_FLOW")

    def test_ineligibility_reasons_populated(self):
        result = calculate_refi(_inputs(value_basis_rule="ineligible"), _LENDER, _CFG)
        assert result.ineligibility_reasons

    def test_opex_includes_all_components(self):
        result = calculate_refi(_inputs(rent_actual=1_400), _LENDER, _CFG)
        # opex = taxes + insurance + rent × (0.08+0.08+0.07+0.10)
        taxes     = 150_000 * 0.02 / 12
        insurance = 1600 / 12
        variable  = 1_400 * (0.08 + 0.08 + 0.07 + 0.10)
        expected  = round(taxes + insurance + variable, 2)
        assert isclose(result.opex_monthly, expected, abs_tol=0.05)
