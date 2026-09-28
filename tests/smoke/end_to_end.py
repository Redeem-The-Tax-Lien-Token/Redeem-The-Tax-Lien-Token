"""
Smoke test — both tracks, dry-run mode.

Runs with SYSTEM_MODE=dry_run (the default when no env var is set).
Imports every major module and exercises the critical path once.
No live DB, no Twilio, no external APIs.

Failure here means something broke at the import or wiring level.
"""

from __future__ import annotations

import importlib
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ── Import smoke (all agents must import cleanly) ─────────────────────────────

AGENT_MODULES = [
    "core.strategy.wholesale",
    "core.strategy.brrrr",
    "core.strategy.eligibility",
    "core.strategy.offer_policy",
    "shared.state_machine",
    "shared.compliance.disclosures",
    "shared.compliance.linter",
    "agents.acquisition.due_diligence",
    "agents.acquisition.lender_packet",
    "agents.rehab.scope_builder",
    "agents.rehab.contractor",
    "agents.rehab.draws",
    "agents.rehab.gate_c_queue",
    "agents.leasing.fair_housing",
    "agents.leasing.screening",
    "agents.refinance.seasoning",
    "agents.refinance.refi_calculator",
    "agents.portfolio.performance",
]


@pytest.mark.parametrize("module_path", AGENT_MODULES)
def test_module_imports(module_path: str):
    """Every core module must import without error."""
    mod = importlib.import_module(module_path)
    assert mod is not None


# ── Wholesale track smoke ─────────────────────────────────────────────────────

def test_wholesale_track_smoke():
    """Wholesale: compute → eligibility → offer → gate → state path."""
    from core.strategy.wholesale import compute_wholesale
    from core.strategy.eligibility import check_wholesale
    from core.strategy.offer_policy import select_offer_price
    from shared.state_machine import LEAD_TRANSITIONS, DEAL_TRANSITIONS, requires_gate
    from shared.compliance.disclosures import build_seller_sms
    from shared.compliance.linter import check_outbound

    # Compute and verify
    case = compute_wholesale(200_000, 20_000, "B")
    assert case.offer_price > 0

    elig = check_wholesale(case, available_buyers=5, seller_timeline_days=30)
    assert elig.all_pass is True

    price = select_offer_price(case, elig)
    assert price == case.offer_price

    # Disclosure — verify text is present (linter may flag segment length; that's expected)
    from shared.compliance.disclosures import load_solicitation_disclosure
    msg, version = build_seller_sms(f"We'd like to offer ${int(price):,} for your property.")
    disc_text, _ = load_solicitation_disclosure()
    assert disc_text in msg
    assert version != ""
    # Linter correctly flags missing disclosure on a bare message
    lint_bare = check_outbound("Plain message, no disclosure.", "")
    assert lint_bare.ok is False

    # State path
    for from_s, to_s in [
        ("new", "scored"), ("scored", "skip_traced"), ("skip_traced", "outreach_active"),
        ("outreach_active", "responded"), ("responded", "hot"),
        ("hot", "underwriting"), ("underwriting", "strategy_selected"),
        ("strategy_selected", "offer_ready"),
    ]:
        assert to_s in LEAD_TRANSITIONS[from_s]

    assert requires_gate("offer_ready") == "GATE_A"

    for from_s, to_s in [
        ("under_contract", "in_dispo"), ("in_dispo", "buyer_selected"),
        ("buyer_selected", "assigned"), ("assigned", "title_open_w"),
        ("title_open_w", "clear_to_close_w"), ("clear_to_close_w", "closed_w"),
        ("closed_w", "fee_received"),
    ]:
        assert to_s in DEAL_TRANSITIONS[from_s]

    assert requires_gate("buyer_selected") == "GATE_B"
    assert DEAL_TRANSITIONS["fee_received"] == set()


# ── BRRRR track smoke ─────────────────────────────────────────────────────────

def test_brrrr_track_smoke():
    """BRRRR: compute → DD → scope → contractor → draw → season → refi → state path."""
    from core.strategy.brrrr import compute_brrrr, brrrr_max_price
    from agents.acquisition.due_diligence import initial_dd_rows, can_advance_to_funding
    from agents.rehab.scope_builder import build_scope_from_estimate, validate_scope
    from agents.rehab.contractor import qualify_contractor
    from agents.rehab.draws import validate_draw_request
    from agents.refinance.seasoning import check_seasoning, months_between
    from agents.refinance.refi_calculator import calculate_refi
    from agents.leasing.fair_housing import lint_listing
    from agents.leasing.screening import evaluate_application
    from shared.state_machine import DEAL_TRANSITIONS, requires_gate

    # BRRRR deal
    case = compute_brrrr(33_000, 30_000, 200_000, 1_800, "MEDIUM")
    assert case.eligible is True

    # DD
    rows = [{**r, "status": "pass"} for r in initial_dd_rows(1)]
    assert can_advance_to_funding(rows) is True

    # Scope
    items = build_scope_from_estimate(30_000, "MEDIUM")
    assert validate_scope(items) == []

    # Contractor
    c = {
        "id": 1, "name": "A+ Contractors",
        "license_verified": True, "insurance_verified": True,
        "insurance_expiry": "2027-12-31", "probation": False, "active": True,
    }
    assert qualify_contractor(c, today=date(2026, 9, 28)).qualified is True

    # Draw
    dv = validate_draw_request(
        {"draw_number": 1, "amount_requested": 12_000, "description": "Phase 1"},
        deal_budget=30_000, draws_so_far=[]
    )
    assert dv.valid is True

    # Listing lint
    assert lint_listing("Updated 3BR home. New kitchen. Close to schools.").ok is True

    # Screening
    app = {
        "monthly_income": 6000, "credit_score": 640, "debt_to_income": 0.30,
        "eviction_history": False, "prior_landlord_reference": True,
    }
    criteria = {
        "min_income_multiple_of_rent": 3.0, "min_credit_score": 580,
        "max_debt_to_income_pct": 0.45, "eviction_history_years": 7,
        "criminal_individualized_assessment": True,
        "prior_landlord_reference_required": True,
    }
    assert evaluate_application(app, monthly_rent=1_800, criteria=criteria).decision == "approved"

    # Seasoning
    lender_prof = {
        "id": "dscr_default", "min_seasoning_months": 3,
        "full_value_seasoning_months": 6, "early_rule": "cost_basis_value",
    }
    sea = check_seasoning(date(2026, 3, 1), lender_prof, now=date(2026, 9, 1))
    assert sea.eligible is True

    # Refi calc
    refi_inputs = {
        "arv": 200_000, "purchase": 33_000, "buy_closing": case.buy_closing,
        "repair_estimate": 30_000, "rent_actual": 1_800,
        "months_owned": int(case.months_owned),
        "value_basis_rule": "full_value", "all_in_cost": case.all_in_cost,
    }
    lp = {
        "refi_ltv": 0.75, "rate": 0.0725, "term_years": 30,
        "cost_pct": 0.025, "fixed_fees": 2000,
        "min_loan": 75_000, "max_loan": 1_500_000, "rent_haircut": 1.0,
    }
    rc = {
        "brrrr": {"min_dscr": 1.25, "min_monthly_cash_flow": 200},
        "opex":  {"vacancy_rate": 0.08, "maintenance_rate": 0.08,
                  "capex_rate": 0.07, "management_rate": 0.10},
        "acquisition": {"tax_pct": 0.02, "insurance_annual": 1600},
    }
    refi = calculate_refi(refi_inputs, lp, rc)
    assert refi.eligible is True

    # State machine path
    for from_s, to_s in [
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
    ]:
        assert to_s in DEAL_TRANSITIONS[from_s], f"{from_s} → {to_s} missing"

    assert requires_gate("appraised") == "GATE_B"
    assert DEAL_TRANSITIONS["stabilized"] == set()


# ── Compliance smoke ──────────────────────────────────────────────────────────

def test_disclosure_smoke():
    """HB 1068 disclosure is non-empty and both compliance modules load correctly."""
    from shared.compliance.disclosures import (
        INDIANA_HB1068_DISCLOSURE, TCPA_OPT_OUT_FOOTER, build_seller_sms,
    )
    from shared.compliance.linter import check_outbound

    assert INDIANA_HB1068_DISCLOSURE != ""
    assert TCPA_OPT_OUT_FOOTER != ""

    from shared.compliance.disclosures import load_solicitation_disclosure
    msg, ver = build_seller_sms("We're interested in your property at 456 Oak St.")
    disc_text, _ = load_solicitation_disclosure()
    assert disc_text in msg
    assert ver != ""
    # Linter blocks a message missing the disclosure entirely
    assert not check_outbound("No disclosure here.", "").ok


def test_fair_housing_smoke():
    """Fair housing linter and guard load and pass on clean inputs."""
    from agents.leasing.fair_housing import lint_listing, guard_application_fields

    assert lint_listing("Beautiful home near downtown.").ok is True
    guard_application_fields({"monthly_income": 5000, "credit_score": 650})


def test_state_machine_smoke():
    """State machine loads cleanly and gate states are consistent."""
    from shared.state_machine import GATE_STATES, all_lead_statuses, all_deal_statuses

    all_s = all_lead_statuses() | all_deal_statuses()
    for gs in GATE_STATES:
        assert gs in all_s
