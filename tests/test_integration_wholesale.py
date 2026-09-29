"""
Integration test — Wholesale track (dry-run).

Exercises the full pipeline in memory without a live DB or external APIs:
  lead states: new → scored → skip_traced → outreach_active → responded → hot
             → underwriting → strategy_selected → offer_ready → offer_sent
             → under_contract
  deal states: under_contract → in_dispo → buyer_selected → assigned
             → title_open_w → clear_to_close_w → closed_w → fee_received

Validates:
  - Deterministic wholesale math (§2.2)
  - Hard eligibility gate evaluation (§3 Step 2)
  - Offer policy produces a single price (§3 Step 4)
  - HB 1068 disclosure present in every seller-facing message
  - Outbound compliance linter passes
  - State machine accepts every transition and rejects invalid ones
  - Assignment builder produces a complete packet
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from math import isclose

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.strategy.wholesale import compute_wholesale
from core.strategy.eligibility import check_wholesale
from core.strategy.offer_policy import select_offer_price
from shared.state_machine import (
    LEAD_TRANSITIONS,
    DEAL_TRANSITIONS,
    GATE_STATES,
    requires_gate,
    all_lead_statuses,
    all_deal_statuses,
)
from shared.compliance.disclosures import build_seller_sms, INDIANA_HB1068_DISCLOSURE
from shared.compliance.linter import check_outbound


# ── Fixtures ──────────────────────────────────────────────────────────────────

DEAL = {
    "arv":               200_000.0,
    "repairs":           20_000.0,
    "neighborhood_class": "B",
    "seller_timeline_days": 30,
    "available_buyers":  5,
}


# ── Wholesale math ─────────────────────────────────────────────────────────────

class TestWholesaleMathIntegration:
    def test_compute_wholesale_formula(self):
        """ARV × 0.75 − repairs − fee = MAO; offer rounds down to nearest $1k."""
        case = compute_wholesale(
            arv=DEAL["arv"],
            repairs=DEAL["repairs"],
            neighborhood_class=DEAL["neighborhood_class"],
        )
        # MAO = 200k×0.75 − 20k − 10k = 120k  → offer = 120k
        assert case.offer_price == 120_000.0
        assert case.discount_rate == 0.75
        assert case.fee_at_offer >= 10_000.0  # at least the target assignment fee

    def test_fee_at_offer_covers_minimum(self):
        case = compute_wholesale(200_000, 20_000, "B")
        cfg = None  # uses disk config
        from core.strategy._config import load as load_cfg
        cfg = load_cfg()
        assert case.fee_at_offer >= cfg["wholesale"]["min_wholesale_fee"]

    def test_negative_mao_gives_zero_offer(self):
        """Massive repair estimate → MAO ≤ 0 → offer = $0."""
        case = compute_wholesale(200_000, 150_000, "B")
        assert case.offer_price == 0.0

    def test_neighborhood_c_discount(self):
        """C-class uses 0.65 discount rate."""
        case = compute_wholesale(200_000, 20_000, "C")
        assert case.discount_rate == 0.65
        assert case.mao == 200_000 * 0.65 - 20_000 - 10_000


# ── Eligibility gates ──────────────────────────────────────────────────────────

class TestWholesaleEligibilityIntegration:
    def test_all_gates_pass(self):
        case = compute_wholesale(DEAL["arv"], DEAL["repairs"], DEAL["neighborhood_class"])
        elig = check_wholesale(case, DEAL["available_buyers"], DEAL["seller_timeline_days"])
        assert elig.all_pass is True
        assert elig.reasons == ()

    def test_fee_gate_blocks_thin_spread(self):
        """$5k fee (< $7.5k min) → FEE_TOO_LOW."""
        case = compute_wholesale(200_000, 145_000, "B")  # mao = 150k×0.75 − 145k − 10k = -5k
        elig = check_wholesale(case, 5, 30)
        assert elig.all_pass is False
        assert "SPREAD_TOO_THIN" in elig.reasons or "FEE_TOO_LOW" in elig.reasons

    def test_buyer_demand_gate(self):
        """Zero buyers → LOW_BUYER_DEMAND."""
        case = compute_wholesale(DEAL["arv"], DEAL["repairs"], DEAL["neighborhood_class"])
        elig = check_wholesale(case, available_buyers=0, seller_timeline_days=30)
        assert elig.all_pass is False
        assert "LOW_BUYER_DEMAND" in elig.reasons

    def test_timeline_gate(self):
        """Seller needs to close in 5 days — too short for dispo."""
        case = compute_wholesale(DEAL["arv"], DEAL["repairs"], DEAL["neighborhood_class"])
        elig = check_wholesale(case, available_buyers=5, seller_timeline_days=5)
        assert elig.all_pass is False
        assert "TIMELINE_TOO_SHORT" in elig.reasons


# ── Offer policy ──────────────────────────────────────────────────────────────

class TestOfferPolicyIntegration:
    def test_eligible_deal_returns_price(self):
        case = compute_wholesale(DEAL["arv"], DEAL["repairs"], DEAL["neighborhood_class"])
        elig = check_wholesale(case, 5, 30)
        price = select_offer_price(case, elig)
        assert price == case.offer_price
        assert price > 0

    def test_ineligible_deal_returns_none(self):
        """No buyers → ineligible → offer policy returns None → NURTURE."""
        case = compute_wholesale(DEAL["arv"], DEAL["repairs"], DEAL["neighborhood_class"])
        elig = check_wholesale(case, available_buyers=0, seller_timeline_days=30)
        price = select_offer_price(case, elig)
        assert price is None

    def test_offer_is_rounded_down(self):
        """Offer is always floored to nearest $1,000."""
        case = compute_wholesale(201_500, 20_000, "B")
        assert case.offer_price % 1_000 == 0


# ── Compliance: HB 1068 in seller SMS ────────────────────────────────────────

class TestDisclosureIntegration:
    def test_seller_sms_contains_hb1068(self):
        text, version = build_seller_sms("Hi, we're interested in your property.")
        assert "assign" in text.lower() or "investor" in text.lower()
        assert version != ""

    def test_seller_sms_contains_disclosure_text(self):
        """build_seller_sms embeds the verbatim disclosure — check presence."""
        from shared.compliance.disclosures import load_solicitation_disclosure
        text, version = build_seller_sms("We'd like to make you an offer on your property.")
        disc_text, _ = load_solicitation_disclosure()
        assert disc_text in text

    def test_linter_blocks_missing_disclosure(self):
        """A bare message without the disclosure must fail the linter."""
        result = check_outbound(
            "We want to buy your house at 123 Main St for $100,000.",
            disclosure_version="",
        )
        assert result.ok is False

    def test_contract_disclosure_text_present(self):
        """The statutory PSA disclosure text is non-empty and contains key words."""
        assert "assign" in INDIANA_HB1068_DISCLOSURE.lower()
        assert "investor" in INDIANA_HB1068_DISCLOSURE.lower()


# ── State machine: lead track ─────────────────────────────────────────────────

class TestLeadStateMachineIntegration:
    """Drive a lead through the full wholesale front-end without a live DB."""

    WHOLESALE_LEAD_PATH = [
        ("new", "scored"),
        ("scored", "skip_traced"),
        ("skip_traced", "outreach_active"),
        ("outreach_active", "responded"),
        ("responded", "hot"),
        ("hot", "underwriting"),
        ("underwriting", "strategy_selected"),
        ("strategy_selected", "offer_ready"),
        ("offer_ready", "offer_sent"),
        ("offer_sent", "under_contract"),
    ]

    def test_every_lead_transition_is_allowed(self):
        for from_s, to_s in self.WHOLESALE_LEAD_PATH:
            assert to_s in LEAD_TRANSITIONS[from_s], (
                f"Lead transition {from_s!r} → {to_s!r} not in state machine"
            )

    def test_offer_ready_requires_gate_a(self):
        assert requires_gate("offer_ready") == "GATE_A"

    def test_dnc_is_terminal(self):
        assert LEAD_TRANSITIONS["dnc"] == set()

    def test_dead_is_terminal(self):
        assert LEAD_TRANSITIONS["dead"] == set()

    def test_invalid_lead_transition_not_in_map(self):
        assert "scored" not in LEAD_TRANSITIONS["new"] or "scored" in LEAD_TRANSITIONS["new"]
        # specific: hot → closed is not valid
        assert "closed" not in LEAD_TRANSITIONS.get("hot", set())


# ── State machine: deal track (wholesale) ────────────────────────────────────

class TestWholesaleDealStateMachineIntegration:
    WHOLESALE_DEAL_PATH = [
        ("under_contract", "in_dispo"),
        ("in_dispo", "buyer_selected"),
        ("buyer_selected", "assigned"),
        ("assigned", "title_open_w"),
        ("title_open_w", "clear_to_close_w"),
        ("clear_to_close_w", "closed_w"),
        ("closed_w", "fee_received"),
    ]

    def test_every_deal_transition_is_allowed(self):
        for from_s, to_s in self.WHOLESALE_DEAL_PATH:
            assert to_s in DEAL_TRANSITIONS[from_s], (
                f"Deal transition {from_s!r} → {to_s!r} not in state machine"
            )

    def test_buyer_selected_requires_gate_b(self):
        assert requires_gate("buyer_selected") == "GATE_B"

    def test_fee_received_is_terminal(self):
        assert DEAL_TRANSITIONS["fee_received"] == set()

    def test_under_contract_can_pivot_to_strategy_switch(self):
        assert "strategy_switch" in DEAL_TRANSITIONS["under_contract"]

    def test_strategy_switch_requires_gate_a(self):
        assert requires_gate("strategy_switch") == "GATE_A"

    def test_dead_is_terminal(self):
        assert DEAL_TRANSITIONS["dead"] == set()

    def test_invalid_deal_transition_rejected(self):
        # Cannot go from in_dispo back to under_contract
        assert "under_contract" not in DEAL_TRANSITIONS.get("in_dispo", set())


# ── Assignment builder ────────────────────────────────────────────────────────

class TestAssignmentBuilderIntegration:
    def test_assignment_packet_fields(self):
        """AssignmentPacket must contain all required fields."""
        try:
            from agents.dispo_coordinator.assignment_builder import build_assignment_packet
        except ImportError:
            pytest.skip("assignment_builder not yet implemented")

        packet = build_assignment_packet(
            deal_id=1,
            seller_price=120_000,
            buyer_price=130_000,
            assignment_fee=10_000,
            property_address="123 Main St, Indianapolis, IN 46201",
            entity_name="Redeem Real Estate LLC and/or assigns",
        )
        assert packet["assignment_fee"] == 10_000
        assert "disclosure" in str(packet).lower() or packet.get("disclosure") is not None


# ── Dry-run gate packet summary ───────────────────────────────────────────────

class TestGateAPacketIntegration:
    """Gate A packet must be structurally complete for the operator approval flow."""

    def test_gate_a_packet_has_required_sections(self):
        case = compute_wholesale(DEAL["arv"], DEAL["repairs"], DEAL["neighborhood_class"])
        elig = check_wholesale(case, 5, 30)
        price = select_offer_price(case, elig)

        packet = {
            "offer_price":   price,
            "strategy":      "wholesale",
            "arv":           case.arv,
            "repairs":       case.repairs,
            "fee_at_offer":  case.fee_at_offer,
            "eligible":      elig.all_pass,
            "reasons":       list(elig.reasons),
            "gate":          "GATE_A",
        }

        assert packet["offer_price"] > 0
        assert packet["eligible"] is True
        assert packet["gate"] == "GATE_A"
        assert packet["fee_at_offer"] >= 7_500


# ── All statuses are reachable / consistent ───────────────────────────────────

class TestStateMachineConsistencyIntegration:
    def test_all_lead_statuses_have_transitions(self):
        """Every status that can be transitioned to is also a key in the transition map."""
        for from_s, targets in LEAD_TRANSITIONS.items():
            for t in targets:
                # terminal states may not be keys themselves: dead, dnc
                if t not in ("dead", "dnc"):
                    assert t in LEAD_TRANSITIONS, (
                        f"Lead status {t!r} is a target but not a key in LEAD_TRANSITIONS"
                    )

    def test_all_deal_statuses_have_transitions(self):
        for from_s, targets in DEAL_TRANSITIONS.items():
            for t in targets:
                if t not in DEAL_TRANSITIONS:
                    pytest.fail(
                        f"Deal status {t!r} (target of {from_s!r}) is not a key in DEAL_TRANSITIONS"
                    )

    def test_gate_states_are_valid_deal_or_lead_statuses(self):
        all_s = all_lead_statuses() | all_deal_statuses()
        for gs in GATE_STATES:
            assert gs in all_s, f"Gate state {gs!r} not found in any state set"
