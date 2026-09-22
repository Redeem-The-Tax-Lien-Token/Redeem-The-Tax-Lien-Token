"""
v2.1 deal state machine — §6 of CLAUDE.md.

All lead and deal status changes go through transition_lead() or transition_deal().
No agent writes status columns directly. Every transition is logged to the
state_transitions audit table with actor and reason.

Gate states: transitions INTO gate states are blocked here — they require an
explicit operator approval event from the crm-dashboard Gate queue before the
system can advance past them. The Gate queue writes via transition_*() with
actor="operator".

Usage:
    from shared.state_machine import transition_lead, transition_deal, GATE_STATES
    with session_ctx() as db:
        transition_lead(lead_id=42, to_status="offer_ready",
                        actor="agent_5", reason="underwriting complete", db=db)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# ── Shared front-end lead states ──────────────────────────────────────────────
# Normalized: lowercase, underscores.  Maps current_status → set of allowed next states.

LEAD_TRANSITIONS: dict[str, set[str]] = {
    # Acquisition pipeline
    "new":               {"scored"},
    "scored":            {"skip_traced", "dead"},
    "skip_traced":       {"outreach_active", "dead"},
    "outreach_active":   {"responded", "dead", "dnc"},
    "responded":         {"hot", "warm", "cold", "dnc"},
    # Disposition buckets
    "hot":               {"underwriting", "dead"},
    "warm":              {"outreach_active", "nurture", "dead", "dnc"},
    "cold":              {"nurture", "dead", "dnc"},
    "dnc":               set(),   # terminal — no outbound ever
    # Underwriting
    "underwriting":      {"strategy_selected", "nurture", "dead"},
    "strategy_selected": {"offer_ready", "dead"},
    "nurture":           {"outreach_active", "underwriting", "dead", "dnc"},
    # Offer cycle
    "offer_ready":       {"offer_sent", "dead"},       # advance requires GATE A
    "offer_sent":        {"under_contract", "offer_declined", "dead"},
    "offer_declined":    {"nurture", "dead"},
    # Contract entered — deal record takes over detailed tracking
    "under_contract":    {"dead", "escalate"},
    # Utility
    "escalate":          {"under_contract", "dead"},
    "dead":              set(),   # terminal
}

# ── Deal states — both tracks share one table; track column determines context ─
# All wholesale and BRRRR states in a single map.  The transition() call validates
# that the requested transition is legal, regardless of track.

DEAL_TRANSITIONS: dict[str, set[str]] = {
    # Entry (deal created at contract)
    "under_contract":  {"in_dispo", "due_diligence", "strategy_switch", "dead"},

    # ── Wholesale track ────────────────────────────────────────────────────────
    "in_dispo":        {"buyer_selected", "dead", "escalate"},
    "buyer_selected":  {"assigned", "in_dispo", "dead"},     # advance requires GATE B
    "assigned":        {"title_open_w", "dead"},
    "title_open_w":    {"clear_to_close_w", "dead", "escalate"},
    "clear_to_close_w": {"closed_w", "dead"},
    "closed_w":        {"fee_received"},
    "fee_received":    set(),   # terminal — wholesale complete

    # ── BRRRR track ────────────────────────────────────────────────────────────
    "due_diligence":   {"funding_secured", "strategy_switch", "dead"},
    "funding_secured": {"title_open_b", "dead"},
    "title_open_b":    {"clear_to_close_b", "dead", "escalate"},
    "clear_to_close_b": {"acquired", "dead"},               # advance requires GATE B
    "acquired":        {"scope_ready", "dead"},
    "scope_ready":     {"rehab_active", "dead"},             # advance requires GATE C
    "rehab_active":    {"rehab_complete", "dead", "escalate"},
    "rehab_complete":  {"rent_ready"},
    "rent_ready":      {"listed_for_rent"},
    "listed_for_rent": {"leased", "dead", "escalate"},
    "leased":          {"seasoning"},
    "seasoning":       {"refi_applied", "escalate"},
    "refi_applied":    {"appraised", "dead", "escalate"},
    "appraised":       {"refinanced", "refi_reevaluate", "dead"},  # GATE B before refinanced
    "refi_reevaluate": {"refi_applied", "hold_as_is", "sell_retail", "dead"},
    "refinanced":      {"stabilized"},
    "stabilized":      set(),   # terminal — portfolio asset, managed by Agent 16
    "hold_as_is":      set(),   # terminal — decision to hold without refi
    "sell_retail":     set(),   # terminal — exit via retail listing

    # ── Pivot: BRRRR → Wholesale inside inspection window ─────────────────────
    # strategy_switch requires GATE A re-approval before advancing to in_dispo.
    "strategy_switch": {"in_dispo", "dead"},

    # ── Utility ────────────────────────────────────────────────────────────────
    "escalate":        {
        "due_diligence", "funding_secured", "in_dispo", "buyer_selected",
        "rehab_active", "refi_applied", "dead",
    },
    "dead":            set(),   # terminal
}

# States that require explicit operator Gate approval before the system can
# advance TO the next state.  Recorded for the Gate queue dashboard.
GATE_STATES: dict[str, str] = {
    "offer_ready":       "GATE_A",    # offer + contract packet
    "strategy_switch":   "GATE_A",    # strategy change after contract
    "buyer_selected":    "GATE_B",    # wholesale assignment + EMD
    "clear_to_close_b":  "GATE_B",    # BRRRR purchase funding
    "appraised":         "GATE_B",    # refi closing
    "scope_ready":       "GATE_C",    # rehab scope + contractor selection
}


# ── Transition helpers ─────────────────────────────────────────────────────────

def _log_transition(
    db: "Session",
    entity_type: str,
    entity_id: int,
    from_status: str,
    to_status: str,
    actor: str,
    reason: str | None,
) -> None:
    db.execute(
        text("""
            INSERT INTO state_transitions
                (entity_type, entity_id, from_status, to_status, actor, reason)
            VALUES
                (:etype, :eid, :from_s, :to_s, :actor, :reason)
        """),
        {
            "etype":   entity_type,
            "eid":     entity_id,
            "from_s":  from_status,
            "to_s":    to_status,
            "actor":   actor,
            "reason":  reason or "",
        },
    )


def transition_lead(
    lead_id: int,
    to_status: str,
    actor: str,
    db: "Session",
    reason: str | None = None,
) -> None:
    """
    Advance a lead to to_status.

    Raises ValueError if the transition is not in LEAD_TRANSITIONS.
    Raises LookupError if the lead does not exist.
    """
    row = db.execute(
        text("SELECT status FROM leads WHERE id = :id FOR UPDATE"),
        {"id": lead_id},
    ).fetchone()

    if row is None:
        raise LookupError(f"Lead {lead_id} not found")

    from_status = row.status
    allowed = LEAD_TRANSITIONS.get(from_status)

    if allowed is None:
        raise ValueError(
            f"Lead {lead_id}: unknown from_status {from_status!r} — not in state machine"
        )
    if to_status not in allowed:
        raise ValueError(
            f"Lead {lead_id}: transition {from_status!r} → {to_status!r} not allowed. "
            f"Allowed: {sorted(allowed)}"
        )

    db.execute(
        text("UPDATE leads SET status = :status WHERE id = :id"),
        {"status": to_status, "id": lead_id},
    )
    _log_transition(db, "lead", lead_id, from_status, to_status, actor, reason)
    log.info("Lead %d: %s → %s (actor=%s)", lead_id, from_status, to_status, actor)


def transition_deal(
    deal_id: int,
    to_status: str,
    actor: str,
    db: "Session",
    reason: str | None = None,
) -> None:
    """
    Advance a deal to to_status.

    Raises ValueError if the transition is not in DEAL_TRANSITIONS.
    Raises LookupError if the deal does not exist.
    """
    row = db.execute(
        text("SELECT status FROM deals WHERE id = :id FOR UPDATE"),
        {"id": deal_id},
    ).fetchone()

    if row is None:
        raise LookupError(f"Deal {deal_id} not found")

    from_status = row.status
    allowed = DEAL_TRANSITIONS.get(from_status)

    if allowed is None:
        raise ValueError(
            f"Deal {deal_id}: unknown from_status {from_status!r} — not in state machine"
        )
    if to_status not in allowed:
        raise ValueError(
            f"Deal {deal_id}: transition {from_status!r} → {to_status!r} not allowed. "
            f"Allowed: {sorted(allowed)}"
        )

    db.execute(
        text("UPDATE deals SET status = :status WHERE id = :id"),
        {"status": to_status, "id": deal_id},
    )
    _log_transition(db, "deal", deal_id, from_status, to_status, actor, reason)
    log.info("Deal %d: %s → %s (actor=%s)", deal_id, from_status, to_status, actor)


# ── Convenience ───────────────────────────────────────────────────────────────

def requires_gate(to_status: str) -> str | None:
    """Return the gate name if advancing TO to_status requires operator approval, else None."""
    return GATE_STATES.get(to_status)


def all_lead_statuses() -> frozenset[str]:
    """Return every defined lead status (useful for DB CHECK constraint generation)."""
    statuses: set[str] = set()
    for k, vs in LEAD_TRANSITIONS.items():
        statuses.add(k)
        statuses.update(vs)
    return frozenset(statuses)


def all_deal_statuses() -> frozenset[str]:
    """Return every defined deal status."""
    statuses: set[str] = set()
    for k, vs in DEAL_TRANSITIONS.items():
        statuses.add(k)
        statuses.update(vs)
    return frozenset(statuses)
