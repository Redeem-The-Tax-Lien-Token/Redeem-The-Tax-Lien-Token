"""
Integration test — BRRRR track (dry-run).

Exercises the full BRRRR pipeline in memory without a live DB or external APIs:

  deal states: under_contract → due_diligence → funding_secured → acquired
             → scope_ready → rehab_active → rehab_complete → rent_ready
             → listed_for_rent → leased → seasoning → refi_applied
             → appraised → refinanced → stabilized

Validates:
  - BRRRR math (§2.3): all_in_cost, refi_loan, cash_left_in, DSCR, cash_flow
  - brrrr_max_price() via bisection finds a valid purchase ceiling
  - Seasoning rules: full_value / cost_basis_value / INELIGIBLE branches
  - Refi calculator matches BRRRR calc on same inputs
  - DD checklist: fail-closed (any pending/fail blocks FUNDING_SECURED)
  - Scope builder: line items sum to repair estimate within tolerance
  - Contractor qualification: stale insurance → not qualified
  - Draw validation: total cannot exceed approved budget
  - Gate C change-order validation: any cost increase requires Gate C
  - Fair housing: listing text linter blocks FHA violations
  - Screening: deterministic criteria, no LLM path
  - Seasoning: months_between correctness
  - State machine: every BRRRR state transition is legal
  - Capital recovery: refi_loan − all_in_cost − refi_costs = cash recovered
"""

from __future__ import annotations

import sys
from datetime import date
from math import isclose, isinf
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.strategy.brrrr import compute_brrrr, brrrr_max_price
from core.strategy._lending import load as load_lenders
from agents.acquisition.due_diligence import (
    DD_CHECKS, initial_dd_rows, evaluate_dd_status, can_advance_to_funding,
)
from agents.rehab.scope_builder import build_scope_from_estimate, total_scope_budget, validate_scope
from agents.rehab.contractor import qualify_contractor, rank_bids
from agents.rehab.draws import validate_draw_request
from agents.rehab.gate_c_queue import validate_change_order
from agents.leasing.fair_housing import lint_listing, guard_application_fields
from agents.leasing.screening import evaluate_application, CRITERIA_VERSION
from agents.refinance.seasoning import months_between, check_seasoning
from agents.refinance.refi_calculator import calculate_refi, pv_from_payment
from shared.state_machine import DEAL_TRANSITIONS, GATE_STATES, requires_gate


# ── Canonical integration deal — passes all BRRRR gates ───────────────────────
# purchase=$33k, repairs=$30k, ARV=$200k, rent=$1,800, tier=MEDIUM
# (verified: cash_left_in≈$4,965, DSCR≈1.79, CF=$200)

_PURCHASE  = 33_000.0
_REPAIRS   = 30_000.0
_ARV       = 200_000.0
_RENT      = 1_800.0
_TIER      = "MEDIUM"


# ── BRRRR math ────────────────────────────────────────────────────────────────

class TestBrrrrMathIntegration:
    def test_canonical_deal_eligible(self):
        case = compute_brrrr(_PURCHASE, _REPAIRS, _ARV, _RENT, _TIER)
        assert case.eligible is True

    def test_cash_left_in_within_max(self):
        from core.strategy._config import load as load_cfg
        case = compute_brrrr(_PURCHASE, _REPAIRS, _ARV, _RENT, _TIER)
        cfg  = load_cfg()
        assert case.cash_left_in <= cfg["brrrr"]["max_cash_left_in"]

    def test_dscr_above_minimum(self):
        from core.strategy._config import load as load_cfg
        case = compute_brrrr(_PURCHASE, _REPAIRS, _ARV, _RENT, _TIER)
        cfg  = load_cfg()
        assert case.dscr >= cfg["brrrr"]["min_dscr"]

    def test_cash_flow_above_minimum(self):
        from core.strategy._config import load as load_cfg
        case = compute_brrrr(_PURCHASE, _REPAIRS, _ARV, _RENT, _TIER)
        cfg  = load_cfg()
        assert case.cash_flow >= cfg["brrrr"]["min_monthly_cash_flow"]

    def test_refi_loan_above_min_loan(self):
        lenders = load_lenders()
        case = compute_brrrr(_PURCHASE, _REPAIRS, _ARV, _RENT, _TIER, lenders=lenders)
        min_loan = float(lenders["refi_lenders"][0]["min_loan"])
        assert float(case.refi_loan) >= min_loan

    def test_all_in_cost_components(self):
        """all_in_cost = purchase + repairs + buy_closing + holding + financing + contingency."""
        case = compute_brrrr(_PURCHASE, _REPAIRS, _ARV, _RENT, _TIER)
        reconstructed = (
            case.purchase
            + _REPAIRS
            + case.buy_closing
            + case.holding
            + case.financing_costs
            + case.contingency
        )
        assert isclose(case.all_in_cost, reconstructed, rel_tol=1e-6)

    def test_peak_capital_equals_all_in_minus_acq_loan(self):
        case = compute_brrrr(_PURCHASE, _REPAIRS, _ARV, _RENT, _TIER)
        assert isclose(case.peak_capital, case.all_in_cost - case.acq_loan, rel_tol=1e-6)

    def test_ineligible_value_basis_gives_inf_cash_left_in_in_refi_calc(self):
        """calculate_refi with value_basis_rule='ineligible' → INELIGIBLE → cash_left_in=inf."""
        from agents.refinance.refi_calculator import calculate_refi
        lp = {
            "refi_ltv": 0.75, "rate": 0.0725, "term_years": 30,
            "cost_pct": 0.025, "fixed_fees": 2000,
            "min_loan": 75_000, "max_loan": 1_500_000, "rent_haircut": 1.0,
        }
        rc = {
            "brrrr": {"min_dscr": 1.25, "min_monthly_cash_flow": 200},
            "opex":  {"vacancy_rate": 0.08, "maintenance_rate": 0.08,
                      "capex_rate": 0.07,   "management_rate": 0.10},
            "acquisition": {"tax_pct": 0.02, "insurance_annual": 1600},
        }
        case = compute_brrrr(_PURCHASE, _REPAIRS, _ARV, _RENT, _TIER)
        inputs = {
            "arv": _ARV, "purchase": _PURCHASE, "buy_closing": case.buy_closing,
            "repair_estimate": _REPAIRS, "rent_actual": _RENT,
            "months_owned": 1,           # only 1 month owned
            "value_basis_rule": "ineligible",   # set directly — lender said ineligible
            "all_in_cost": case.all_in_cost,
        }
        result = calculate_refi(inputs, lp, rc)
        assert result.eligible is False
        assert result.refi_loan == "INELIGIBLE"

    def test_brrrr_max_price_passes_all_gates(self):
        """brrrr_max_price result must itself pass all BRRRR gates."""
        max_p = brrrr_max_price(_REPAIRS, _ARV, _RENT, _TIER)
        assert max_p > 0
        case = compute_brrrr(max_p, _REPAIRS, _ARV, _RENT, _TIER)
        assert case.eligible is True

    def test_brrrr_max_price_rounded_to_1k(self):
        max_p = brrrr_max_price(_REPAIRS, _ARV, _RENT, _TIER)
        assert max_p % 1_000 == 0

    def test_value_basis_full_when_seasoned(self):
        """After full seasoning months, value_basis == ARV."""
        case = compute_brrrr(_PURCHASE, _REPAIRS, _ARV, _RENT, _TIER)
        # MEDIUM tier: work_months = 3, full_value_seasoning = 6 → months_owned = 6 → full_value
        assert case.value_basis == _ARV


# ── Capital recovery math ─────────────────────────────────────────────────────

class TestCapitalRecoveryIntegration:
    def test_recovered_capital_formula(self):
        """cash_left_in = all_in_cost + refi_costs − refi_loan."""
        case = compute_brrrr(_PURCHASE, _REPAIRS, _ARV, _RENT, _TIER)
        reconstructed_cli = case.all_in_cost + case.refi_costs - float(case.refi_loan)
        assert isclose(case.cash_left_in, reconstructed_cli, rel_tol=1e-6)

    def test_refi_costs_formula(self):
        """refi_costs = refi_loan × cost_pct + fixed_fees."""
        lenders = load_lenders()
        case = compute_brrrr(_PURCHASE, _REPAIRS, _ARV, _RENT, _TIER, lenders=lenders)
        refi_profile = lenders["refi_lenders"][0]
        expected_costs = (
            float(case.refi_loan) * float(refi_profile["cost_pct"])
            + float(refi_profile["fixed_fees"])
        )
        assert isclose(case.refi_costs, expected_costs, rel_tol=1e-6)


# ── Due diligence ─────────────────────────────────────────────────────────────

class TestDueDiligenceIntegration:
    def test_initial_rows_all_pending(self):
        rows = initial_dd_rows(deal_id=1)
        assert len(rows) == len(DD_CHECKS)
        assert all(r["status"] == "pending" for r in rows)

    def test_pending_blocks_funding(self):
        rows = initial_dd_rows(1)
        assert can_advance_to_funding(rows) is False

    def test_one_fail_blocks_funding(self):
        rows = [{**r, "status": "pass"} if r["check_name"] != "title_search_reviewed"
                else {**r, "status": "fail"}
                for r in initial_dd_rows(1)]
        assert can_advance_to_funding(rows) is False

    def test_all_pass_allows_funding(self):
        rows = [{**r, "status": "pass"} for r in initial_dd_rows(1)]
        assert can_advance_to_funding(rows) is True

    def test_waived_hoa_allows_funding(self):
        rows = [{**r, "status": "pass"} if r["check_name"] != "hoa_check"
                else {**r, "status": "waived"}
                for r in initial_dd_rows(1)]
        assert can_advance_to_funding(rows) is True

    def test_evaluate_dd_status_all_pass(self):
        rows = [{**r, "status": "pass"} for r in initial_dd_rows(1)]
        status = evaluate_dd_status(rows)
        assert status.outcome == "all_pass"

    def test_evaluate_dd_status_has_fail(self):
        rows = initial_dd_rows(1)
        rows[0]["status"] = "fail"
        status = evaluate_dd_status(rows)
        assert status.outcome == "has_fail"
        assert rows[0]["check_name"] in status.failed_checks


# ── Scope builder ─────────────────────────────────────────────────────────────

class TestScopeBuilderIntegration:
    def test_scope_budget_matches_estimate(self):
        items = build_scope_from_estimate(30_000.0, "MEDIUM")
        total = total_scope_budget(items)
        assert isclose(total, 30_000.0, rel_tol=1e-4)

    def test_scope_has_no_negative_costs(self):
        items = build_scope_from_estimate(30_000.0, "MEDIUM")
        assert all(item.total_cost >= 0 for item in items)

    def test_scope_validates_cleanly(self):
        items = build_scope_from_estimate(30_000.0, "MEDIUM")
        errors = validate_scope(items)
        assert errors == []

    def test_empty_scope_fails_validation(self):
        errors = validate_scope([])
        assert errors != []

    def test_gut_tier_has_more_budget_on_foundation(self):
        items_gut    = build_scope_from_estimate(50_000.0, "GUT")
        items_light  = build_scope_from_estimate(50_000.0, "LIGHT")
        gut_found    = next((i for i in items_gut   if i.category == "foundation_structure"), None)
        light_found  = next((i for i in items_light if i.category == "foundation_structure"), None)
        if gut_found and light_found:
            assert gut_found.total_cost >= light_found.total_cost


# ── Contractor qualification ──────────────────────────────────────────────────

class TestContractorQualificationIntegration:
    def test_valid_contractor_qualifies(self):
        contractor = {
            "id": 1, "name": "Smith Rehab LLC",
            "license_verified": True,
            "insurance_verified": True,
            "insurance_expiry": "2027-06-01",
            "probation": False,
            "active": True,
        }
        result = qualify_contractor(contractor, today=date(2026, 9, 28))
        assert result.qualified is True
        assert result.reasons == []

    def test_stale_insurance_disqualifies(self):
        contractor = {
            "id": 2, "name": "Expired Contractor",
            "license_verified": True,
            "insurance_verified": True,
            "insurance_expiry": "2026-01-01",   # expired
            "probation": False,
            "active": True,
        }
        result = qualify_contractor(contractor, today=date(2026, 9, 28))
        assert result.qualified is False

    def test_unverified_license_disqualifies(self):
        contractor = {
            "id": 3, "name": "Unlicensed",
            "license_verified": False,
            "insurance_verified": True,
            "insurance_expiry": "2027-06-01",
            "probation": False,
            "active": True,
        }
        result = qualify_contractor(contractor, today=date(2026, 9, 28))
        assert result.qualified is False

    def test_bid_ranking_lower_cost_first(self):
        bids = [
            {"contractor_id": 1, "bid_amount": 35_000, "timeline_days": 60, "tier_rating": 4},
            {"contractor_id": 2, "bid_amount": 28_000, "timeline_days": 45, "tier_rating": 4},
            {"contractor_id": 3, "bid_amount": 31_000, "timeline_days": 50, "tier_rating": 5},
        ]
        ranked = rank_bids(bids, deal_budget=30_000.0)
        # bids within budget first ($28k), then over-budget ordered by amount
        within = [b for b in ranked if b["bid_amount"] <= 30_000]
        assert within[0]["bid_amount"] == 28_000


# ── Draw validation ───────────────────────────────────────────────────────────

class TestDrawValidationIntegration:
    def test_valid_first_draw(self):
        draw = {"draw_number": 1, "amount_requested": 10_000, "description": "Demo and rough framing"}
        result = validate_draw_request(draw, deal_budget=30_000.0, draws_so_far=[])
        assert result.valid is True

    def test_draw_exceeding_budget_invalid(self):
        prior = [{"draw_number": 1, "amount_requested": 28_000}]
        draw  = {"draw_number": 2, "amount_requested": 5_000, "description": "Final touches"}
        result = validate_draw_request(draw, deal_budget=30_000.0, draws_so_far=prior)
        assert result.valid is False
        assert any("budget" in e.lower() for e in result.errors)

    def test_zero_amount_draw_invalid(self):
        draw = {"draw_number": 1, "amount_requested": 0, "description": "Nothing"}
        result = validate_draw_request(draw, deal_budget=30_000.0, draws_so_far=[])
        assert result.valid is False

    def test_duplicate_draw_number_invalid(self):
        prior = [{"draw_number": 1, "amount_requested": 5_000}]
        draw  = {"draw_number": 1, "amount_requested": 6_000, "description": "Retry"}
        result = validate_draw_request(draw, deal_budget=30_000.0, draws_so_far=prior)
        assert result.valid is False


# ── Gate C change order ───────────────────────────────────────────────────────

class TestGateCIntegration:
    def test_positive_delta_requires_gate_c(self):
        co = {"amount_delta": 2_000, "reason": "Found hidden water damage", "co_number": 1}
        result = validate_change_order(co, deal_budget=30_000.0, approved_cos_so_far=[])
        assert result.requires_gate_c is True

    def test_zero_delta_auto_approved(self):
        co = {"amount_delta": 0, "reason": "Scope clarification, no cost change", "co_number": 1}
        result = validate_change_order(co, deal_budget=30_000.0, approved_cos_so_far=[])
        assert result.requires_gate_c is False

    def test_negative_delta_auto_approved(self):
        co = {"amount_delta": -500, "reason": "Descoped trim work", "co_number": 1}
        result = validate_change_order(co, deal_budget=30_000.0, approved_cos_so_far=[])
        assert result.requires_gate_c is False


# ── Fair housing ──────────────────────────────────────────────────────────────

class TestFairHousingIntegration:
    def test_clean_listing_passes(self):
        text = "Spacious 3BR home near transit, updated kitchen, pet-friendly."
        assert lint_listing(text).ok is True

    def test_no_children_blocked(self):
        result = lint_listing("Adults-only building, no children please.")
        assert result.ok is False
        assert any("familial" in f for f in result.flags)

    def test_source_of_income_blocked(self):
        result = lint_listing("No Section 8 or housing vouchers accepted.")
        assert result.ok is False

    def test_protected_field_in_application_raises(self):
        with pytest.raises(ValueError):
            guard_application_fields({"race": "Hispanic", "monthly_income": 5000})

    def test_clean_application_passes_guard(self):
        guard_application_fields({"monthly_income": 5000, "credit_score": 650})


# ── Tenant screening ──────────────────────────────────────────────────────────

class TestScreeningIntegration:
    _CRITERIA = {
        "min_income_multiple_of_rent": 3.0,
        "min_credit_score": 580,
        "max_debt_to_income_pct": 0.45,
        "eviction_history_years": 7,
        "criminal_individualized_assessment": True,
        "prior_landlord_reference_required": True,
    }

    def test_qualified_applicant_approved(self):
        app = {
            "monthly_income": 6_000,
            "credit_score": 650,
            "debt_to_income": 0.30,
            "eviction_history": False,
            "prior_landlord_reference": True,
        }
        result = evaluate_application(app, monthly_rent=1_800, criteria=self._CRITERIA)
        assert result.decision == "approved"
        assert result.criteria_version == CRITERIA_VERSION

    def test_income_fail_denied(self):
        app = {
            "monthly_income": 3_000,   # < 1800×3 = 5400
            "credit_score": 650,
            "debt_to_income": 0.30,
            "eviction_history": False,
            "prior_landlord_reference": True,
        }
        result = evaluate_application(app, monthly_rent=1_800, criteria=self._CRITERIA)
        assert result.decision == "denied"
        assert any("income" in r.lower() for r in result.reasons)

    def test_missing_credit_score_pending(self):
        app = {
            "monthly_income": 6_000,
            "credit_score": None,
            "debt_to_income": 0.30,
            "eviction_history": False,
            "prior_landlord_reference": True,
        }
        result = evaluate_application(app, monthly_rent=1_800, criteria=self._CRITERIA)
        assert result.decision == "pending"


# ── Seasoning ─────────────────────────────────────────────────────────────────

class TestSeasoningIntegration:
    _LENDER = {
        "id": "dscr_default",
        "min_seasoning_months": 3,
        "full_value_seasoning_months": 6,
        "early_rule": "cost_basis_value",
    }

    def test_full_season_gives_full_value(self):
        acq  = date(2026, 3, 1)
        now  = date(2026, 9, 1)   # exactly 6 months
        res  = check_seasoning(acq, self._LENDER, now=now)
        assert res.eligible is True
        assert res.value_basis_rule == "full_value"

    def test_early_season_gives_cost_basis(self):
        acq  = date(2026, 6, 1)
        now  = date(2026, 9, 1)   # 3 months
        res  = check_seasoning(acq, self._LENDER, now=now)
        assert res.eligible is True
        assert res.value_basis_rule == "cost_basis_value"

    def test_too_early_ineligible(self):
        acq  = date(2026, 7, 15)
        now  = date(2026, 9, 1)   # < 3 months
        res  = check_seasoning(acq, self._LENDER, now=now)
        assert res.eligible is False
        assert res.value_basis_rule == "ineligible"

    def test_months_between_cross_year(self):
        assert months_between(date(2025, 10, 1), date(2026, 4, 1)) == 6


# ── Refi calculator vs. BRRRR calc cross-check ───────────────────────────────

class TestRefiCalcCrossCheckIntegration:
    """calculate_refi() and compute_brrrr() should agree on rent_q and value_basis."""

    _LENDER_PROF = {
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
        "brrrr": {"min_dscr": 1.25, "min_monthly_cash_flow": 200},
        "opex": {
            "vacancy_rate": 0.08,
            "maintenance_rate": 0.08,
            "capex_rate": 0.07,
            "management_rate": 0.10,
        },
        "acquisition": {"tax_pct": 0.02, "insurance_annual": 1600},
    }

    def test_refi_calc_eligible_on_canonical_deal(self):
        case = compute_brrrr(_PURCHASE, _REPAIRS, _ARV, _RENT, _TIER)
        inputs = {
            "arv":             _ARV,
            "purchase":        _PURCHASE,
            "buy_closing":     case.buy_closing,
            "repair_estimate": _REPAIRS,
            "rent_actual":     _RENT,
            "months_owned":    int(case.months_owned),
            "value_basis_rule": "full_value",
            "all_in_cost":     case.all_in_cost,
        }
        result = calculate_refi(inputs, self._LENDER_PROF, self._CFG)
        assert result.eligible is True
        assert isinstance(result.refi_loan, float)
        assert result.refi_loan > 0

    def test_rent_q_matches_when_haircut_is_1(self):
        case = compute_brrrr(_PURCHASE, _REPAIRS, _ARV, _RENT, _TIER)
        inputs = {
            "arv": _ARV, "purchase": _PURCHASE,
            "buy_closing": case.buy_closing, "repair_estimate": _REPAIRS,
            "rent_actual": _RENT, "months_owned": int(case.months_owned),
            "value_basis_rule": "full_value", "all_in_cost": case.all_in_cost,
        }
        result = calculate_refi(inputs, self._LENDER_PROF, self._CFG)
        assert isclose(result.rent_q, _RENT, rel_tol=1e-6)


# ── State machine: BRRRR track ────────────────────────────────────────────────

class TestBrrrrStateMachineIntegration:
    BRRRR_PATH = [
        ("under_contract", "due_diligence"),
        ("due_diligence", "funding_secured"),
        ("funding_secured", "title_open_b"),
        ("title_open_b", "clear_to_close_b"),
        ("clear_to_close_b", "acquired"),
        ("acquired", "scope_ready"),
        ("scope_ready", "rehab_active"),
        ("rehab_active", "rehab_complete"),
        ("rehab_complete", "rent_ready"),
        ("rent_ready", "listed_for_rent"),
        ("listed_for_rent", "leased"),
        ("leased", "seasoning"),
        ("seasoning", "refi_applied"),
        ("refi_applied", "appraised"),
        ("appraised", "refinanced"),
        ("refinanced", "stabilized"),
    ]

    def test_every_brrrr_transition_is_allowed(self):
        for from_s, to_s in self.BRRRR_PATH:
            assert to_s in DEAL_TRANSITIONS[from_s], (
                f"BRRRR transition {from_s!r} → {to_s!r} not in DEAL_TRANSITIONS"
            )

    def test_clear_to_close_b_requires_gate_b(self):
        assert requires_gate("clear_to_close_b") == "GATE_B"

    def test_scope_ready_requires_gate_c(self):
        assert requires_gate("scope_ready") == "GATE_C"

    def test_appraised_requires_gate_b(self):
        assert requires_gate("appraised") == "GATE_B"

    def test_stabilized_is_terminal(self):
        assert DEAL_TRANSITIONS["stabilized"] == set()

    def test_brrrr_can_pivot_to_strategy_switch_from_due_diligence(self):
        assert "strategy_switch" in DEAL_TRANSITIONS["due_diligence"]

    def test_low_appraisal_can_reevaluate(self):
        assert "refi_reevaluate" in DEAL_TRANSITIONS["appraised"]

    def test_refi_reevaluate_options(self):
        """Reevaluate can go to a new lender, hold, sell, or retry."""
        targets = DEAL_TRANSITIONS["refi_reevaluate"]
        assert "refi_applied" in targets    # new lender
        assert "hold_as_is"   in targets
        assert "sell_retail"  in targets

    def test_hold_as_is_is_terminal(self):
        assert DEAL_TRANSITIONS["hold_as_is"] == set()

    def test_sell_retail_is_terminal(self):
        assert DEAL_TRANSITIONS["sell_retail"] == set()


# ── End-to-end dry-run summary ────────────────────────────────────────────────

class TestBrrrrEndToEndDryRun:
    """
    Full BRRRR deal summary in dry-run mode:
    Compute the deal, run DD, scope, qualify contractor, validate draws,
    check seasoning, run refi calc — all without a live DB.
    """

    def test_full_pipeline_passes_with_canonical_deal(self):
        # 1. Strategy calc
        case = compute_brrrr(_PURCHASE, _REPAIRS, _ARV, _RENT, _TIER)
        assert case.eligible is True

        # 2. Due diligence — simulate all checks pass
        dd_rows = [{**r, "status": "pass"} for r in initial_dd_rows(1)]
        assert can_advance_to_funding(dd_rows) is True

        # 3. Scope
        items = build_scope_from_estimate(_REPAIRS, _TIER)
        assert validate_scope(items) == []

        # 4. Contractor
        contractor = {
            "id": 1, "name": "Elite Rehab LLC",
            "license_verified": True, "insurance_verified": True,
            "insurance_expiry": "2027-12-31", "probation": False, "active": True,
        }
        qual = qualify_contractor(contractor, today=date(2026, 9, 28))
        assert qual.qualified is True

        # 5. Draw #1 — 40% upfront
        draw1 = {"draw_number": 1, "amount_requested": _REPAIRS * 0.40, "description": "Mobilization + demo"}
        dv = validate_draw_request(draw1, deal_budget=_REPAIRS, draws_so_far=[])
        assert dv.valid is True

        # 6. Listing lint
        listing = "Updated 3BR/1BA near downtown Indy. Fresh paint, new floors, large backyard."
        assert lint_listing(listing).ok is True

        # 7. Screening
        app = {
            "monthly_income": 6_000, "credit_score": 660,
            "debt_to_income": 0.25, "eviction_history": False,
            "prior_landlord_reference": True,
        }
        criteria = {
            "min_income_multiple_of_rent": 3.0, "min_credit_score": 580,
            "max_debt_to_income_pct": 0.45, "eviction_history_years": 7,
            "criminal_individualized_assessment": True,
            "prior_landlord_reference_required": True,
        }
        screening = evaluate_application(app, monthly_rent=_RENT, criteria=criteria)
        assert screening.decision == "approved"

        # 8. Seasoning check
        lender_profile = {
            "id": "dscr_default",
            "min_seasoning_months": 3,
            "full_value_seasoning_months": 6,
            "early_rule": "cost_basis_value",
        }
        acq_date = date(2026, 3, 1)
        today    = date(2026, 9, 1)   # 6 months
        sea = check_seasoning(acq_date, lender_profile, now=today)
        assert sea.eligible is True
        assert sea.value_basis_rule == "full_value"

        # 9. Refi calc
        refi_inputs = {
            "arv": _ARV, "purchase": _PURCHASE, "buy_closing": case.buy_closing,
            "repair_estimate": _REPAIRS, "rent_actual": _RENT,
            "months_owned": int(case.months_owned),
            "value_basis_rule": sea.value_basis_rule,
            "all_in_cost": case.all_in_cost,
        }
        lender_prof = {
            "refi_ltv": 0.75, "rate": 0.0725, "term_years": 30,
            "cost_pct": 0.025, "fixed_fees": 2000,
            "min_loan": 75_000, "max_loan": 1_500_000, "rent_haircut": 1.0,
        }
        refi_cfg = {
            "brrrr": {"min_dscr": 1.25, "min_monthly_cash_flow": 200},
            "opex":  {"vacancy_rate": 0.08, "maintenance_rate": 0.08,
                      "capex_rate": 0.07,   "management_rate": 0.10},
            "acquisition": {"tax_pct": 0.02, "insurance_annual": 1600},
        }
        refi = calculate_refi(refi_inputs, lender_prof, refi_cfg)
        assert refi.eligible is True
        assert isinstance(refi.refi_loan, float) and refi.refi_loan > 0
        assert refi.cash_left_in <= 10_000
