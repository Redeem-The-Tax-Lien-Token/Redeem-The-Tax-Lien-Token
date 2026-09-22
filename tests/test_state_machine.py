"""
Unit tests for shared/state_machine.py.

Tests cover:
  - Every explicit lead transition in LEAD_TRANSITIONS
  - Every explicit deal transition in DEAL_TRANSITIONS (both tracks)
  - Rejection of transitions not in the map
  - Terminal states reject all moves
  - Gate state classification
  - transition_lead() DB path: happy path, bad transition, missing lead
  - transition_deal() DB path: happy path, bad transition, missing deal
  - all_lead_statuses / all_deal_statuses helpers
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.state_machine import (
    DEAL_TRANSITIONS,
    GATE_STATES,
    LEAD_TRANSITIONS,
    all_deal_statuses,
    all_lead_statuses,
    requires_gate,
    transition_deal,
    transition_lead,
)


# ── Transition map completeness ────────────────────────────────────────────────

def test_lead_transitions_is_dict():
    assert isinstance(LEAD_TRANSITIONS, dict)
    assert len(LEAD_TRANSITIONS) >= 10


def test_deal_transitions_is_dict():
    assert isinstance(DEAL_TRANSITIONS, dict)
    assert len(DEAL_TRANSITIONS) >= 15


def test_lead_starts_at_new():
    assert "new" in LEAD_TRANSITIONS


def test_deal_starts_at_under_contract():
    assert "under_contract" in DEAL_TRANSITIONS


def test_dnc_is_terminal():
    assert LEAD_TRANSITIONS["dnc"] == set()


def test_dead_is_terminal_lead():
    assert LEAD_TRANSITIONS["dead"] == set()


def test_dead_is_terminal_deal():
    assert DEAL_TRANSITIONS["dead"] == set()


def test_fee_received_is_terminal():
    assert DEAL_TRANSITIONS["fee_received"] == set()


def test_stabilized_is_terminal():
    assert DEAL_TRANSITIONS["stabilized"] == set()


# ── Key transitions exist ─────────────────────────────────────────────────────

@pytest.mark.parametrize("from_s,to_s", [
    ("new",           "scored"),
    ("scored",        "skip_traced"),
    ("skip_traced",   "outreach_active"),
    ("outreach_active", "responded"),
    ("responded",     "hot"),
    ("responded",     "warm"),
    ("responded",     "cold"),
    ("responded",     "dnc"),
    ("hot",           "underwriting"),
    ("underwriting",  "strategy_selected"),
    ("underwriting",  "nurture"),
    ("strategy_selected", "offer_ready"),
    ("offer_ready",   "offer_sent"),
    ("offer_sent",    "under_contract"),
    ("offer_sent",    "offer_declined"),
    ("offer_declined", "nurture"),
    ("warm",          "nurture"),
    ("cold",          "nurture"),
    ("nurture",       "outreach_active"),
])
def test_lead_transition_allowed(from_s, to_s):
    assert to_s in LEAD_TRANSITIONS[from_s], (
        f"Expected {from_s!r} → {to_s!r} to be allowed"
    )


@pytest.mark.parametrize("from_s,to_s", [
    # Wholesale track
    ("under_contract",   "in_dispo"),
    ("in_dispo",         "buyer_selected"),
    ("buyer_selected",   "assigned"),
    ("assigned",         "title_open_w"),
    ("title_open_w",     "clear_to_close_w"),
    ("clear_to_close_w", "closed_w"),
    ("closed_w",         "fee_received"),
    # BRRRR track
    ("under_contract",   "due_diligence"),
    ("due_diligence",    "funding_secured"),
    ("funding_secured",  "title_open_b"),
    ("title_open_b",     "clear_to_close_b"),
    ("clear_to_close_b", "acquired"),
    ("acquired",         "scope_ready"),
    ("scope_ready",      "rehab_active"),
    ("rehab_active",     "rehab_complete"),
    ("rehab_complete",   "rent_ready"),
    ("rent_ready",       "listed_for_rent"),
    ("listed_for_rent",  "leased"),
    ("leased",           "seasoning"),
    ("seasoning",        "refi_applied"),
    ("refi_applied",     "appraised"),
    ("appraised",        "refinanced"),
    ("refinanced",       "stabilized"),
    # Pivot
    ("under_contract",   "strategy_switch"),
    ("due_diligence",    "strategy_switch"),
    ("strategy_switch",  "in_dispo"),
    # Low-appraisal exits
    ("appraised",        "refi_reevaluate"),
    ("refi_reevaluate",  "hold_as_is"),
    ("refi_reevaluate",  "sell_retail"),
    ("refi_reevaluate",  "refi_applied"),
])
def test_deal_transition_allowed(from_s, to_s):
    assert to_s in DEAL_TRANSITIONS[from_s], (
        f"Expected deal {from_s!r} → {to_s!r} to be allowed"
    )


# ── Rejected transitions ──────────────────────────────────────────────────────

@pytest.mark.parametrize("from_s,bad_to", [
    ("new",       "hot"),           # must go through scored first
    ("dnc",       "outreach_active"),  # terminal
    ("dead",      "new"),           # terminal
    ("scored",    "offer_sent"),    # skips required steps
    ("warm",      "under_contract"),   # not valid from warm
])
def test_lead_transition_rejected(from_s, bad_to):
    assert bad_to not in LEAD_TRANSITIONS.get(from_s, set()), (
        f"Expected {from_s!r} → {bad_to!r} to be REJECTED"
    )


@pytest.mark.parametrize("from_s,bad_to", [
    ("fee_received",  "in_dispo"),    # terminal
    ("stabilized",    "rehab_active"), # terminal
    ("in_dispo",      "acquired"),    # wrong track jump
    ("due_diligence", "buyer_selected"),  # wrong track
])
def test_deal_transition_rejected(from_s, bad_to):
    assert bad_to not in DEAL_TRANSITIONS.get(from_s, set()), (
        f"Expected deal {from_s!r} → {bad_to!r} to be REJECTED"
    )


# ── Gate states ───────────────────────────────────────────────────────────────

def test_offer_ready_requires_gate_a():
    assert requires_gate("offer_ready") == "GATE_A"


def test_buyer_selected_requires_gate_b():
    assert requires_gate("buyer_selected") == "GATE_B"


def test_scope_ready_requires_gate_c():
    assert requires_gate("scope_ready") == "GATE_C"


def test_strategy_switch_requires_gate_a():
    assert requires_gate("strategy_switch") == "GATE_A"


def test_non_gate_state_returns_none():
    assert requires_gate("in_dispo") is None
    assert requires_gate("leased") is None
    assert requires_gate("new") is None


# ── Status set helpers ────────────────────────────────────────────────────────

def test_all_lead_statuses_contains_key_states():
    statuses = all_lead_statuses()
    for s in ("new", "hot", "dnc", "under_contract", "dead", "offer_sent"):
        assert s in statuses, f"Expected {s!r} in all_lead_statuses()"


def test_all_deal_statuses_contains_key_states():
    statuses = all_deal_statuses()
    for s in ("under_contract", "in_dispo", "fee_received", "acquired",
              "stabilized", "strategy_switch"):
        assert s in statuses, f"Expected {s!r} in all_deal_statuses()"


# ── DB-path unit tests (mock DB) ───────────────────────────────────────────────

def _mock_db(current_status: str):
    """Build a mock SQLAlchemy Session that returns current_status for SELECT."""
    row = MagicMock()
    row.status = current_status

    execute_result = MagicMock()
    execute_result.fetchone.return_value = row

    db = MagicMock()
    db.execute.return_value = execute_result
    return db


def test_transition_lead_happy_path():
    db = _mock_db("scored")
    transition_lead(lead_id=1, to_status="skip_traced", actor="agent_2", db=db)
    # Should call execute twice: SELECT FOR UPDATE, then UPDATE
    assert db.execute.call_count == 3  # SELECT + UPDATE + INSERT


def test_transition_lead_bad_transition_raises():
    db = _mock_db("dnc")
    with pytest.raises(ValueError, match="not allowed"):
        transition_lead(lead_id=1, to_status="outreach_active", actor="agent_3", db=db)


def test_transition_lead_missing_lead_raises():
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = None
    with pytest.raises(LookupError, match="Lead 99 not found"):
        transition_lead(lead_id=99, to_status="scored", actor="agent_1", db=db)


def test_transition_deal_happy_path():
    db = _mock_db("under_contract")
    transition_deal(deal_id=5, to_status="in_dispo", actor="agent_6", db=db)
    assert db.execute.call_count == 3  # SELECT + UPDATE + INSERT


def test_transition_deal_bad_transition_raises():
    db = _mock_db("stabilized")
    with pytest.raises(ValueError, match="not allowed"):
        transition_deal(deal_id=5, to_status="rehab_active", actor="agent_13", db=db)


def test_transition_deal_missing_deal_raises():
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = None
    with pytest.raises(LookupError, match="Deal 42 not found"):
        transition_deal(deal_id=42, to_status="in_dispo", actor="agent_6", db=db)


# ── dead is reachable from every non-terminal active state ────────────────────

def test_dead_reachable_from_major_lead_states():
    active = ["scored", "skip_traced", "outreach_active", "hot", "warm", "cold",
              "underwriting", "strategy_selected", "offer_ready", "offer_sent"]
    for s in active:
        assert "dead" in LEAD_TRANSITIONS[s], (
            f"'dead' should be reachable from lead state {s!r}"
        )


def test_dead_reachable_from_major_deal_states():
    active = ["under_contract", "in_dispo", "due_diligence", "funding_secured",
              "rehab_active", "refi_applied"]
    for s in active:
        assert "dead" in DEAL_TRANSITIONS[s], (
            f"'dead' should be reachable from deal state {s!r}"
        )
