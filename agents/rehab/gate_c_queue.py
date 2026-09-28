"""
Gate C queue for Agent 13 — scope and contractor approval, plus change orders.

Gate C covers three things (all require operator sign-off):
  1. Initial scope-of-work + contractor selection (lead status: scope_ready)
  2. Change orders that exceed the approved budget
  3. Over-budget draws (these actually go to Gate B; change orders go here)

A gate never auto-approves.  Silence = no action (§2.1 ADR-003).

Public API:
    fetch_gate_c_queue(db) -> list[dict]
    fetch_gate_c_item(db, deal_id) -> dict | None
    fetch_change_orders_pending(db, deal_id) -> list[dict]
    record_gate_c_decision(db, deal_id, approved, approved_by, reason=None)
    record_co_decision(db, co_id, approved, approved_by, reason=None)
    ChangeOrderValidation
    validate_change_order(co, deal_budget, approved_cos_so_far) -> ChangeOrderValidation
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text


@dataclass(frozen=True)
class ChangeOrderValidation:
    valid:          bool
    requires_gate_c: bool
    errors:         list[str] = field(default_factory=list)


def validate_change_order(
    co: dict,
    deal_budget: float,
    approved_cos_so_far: list[dict],
) -> ChangeOrderValidation:
    """
    Validate a change order and determine if Gate C re-approval is needed.

    co keys: amount_delta (float), reason (str), co_number (int)
    approved_cos_so_far: list of previously approved change orders

    Gate C required if: new_total > deal_budget (any cost increase).
    """
    errors: list[str] = []

    delta = float(co.get("amount_delta") or 0)
    if not (co.get("reason") or "").strip():
        errors.append("reason is required")

    co_number = co.get("co_number")
    existing = {c.get("co_number") for c in approved_cos_so_far}
    if co_number in existing:
        errors.append(f"co_number {co_number} already exists for this deal")

    previous_co_total = sum(float(c.get("amount_delta") or 0) for c in approved_cos_so_far)
    new_total         = round(deal_budget + previous_co_total + delta, 2)

    requires_gate_c = delta > 0  # any cost increase requires Gate C

    if errors:
        return ChangeOrderValidation(valid=False, requires_gate_c=requires_gate_c, errors=errors)
    return ChangeOrderValidation(valid=True, requires_gate_c=requires_gate_c, errors=[])


# ── DB helpers ────────────────────────────────────────────────────────────────

def fetch_gate_c_queue(db) -> list[dict]:
    """Return deals in scope_ready status, oldest first."""
    rows = db.execute(
        text("""
            SELECT d.id AS deal_id, l.address, l.city, l.owner_name,
                   d.total_rehab_budget, d.gate_c_approved_at,
                   l.status AS lead_status, l.updated_at
            FROM deals d
            JOIN leads l ON l.id = d.lead_id
            WHERE l.status = 'scope_ready'
            ORDER BY l.updated_at ASC
        """)
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def fetch_gate_c_item(db, deal_id: int) -> dict | None:
    row = db.execute(
        text("""
            SELECT d.id AS deal_id, l.address, l.city, l.owner_name,
                   d.total_rehab_budget, d.gate_c_approved_at,
                   d.selected_contractor_id, d.underwriting_snapshot,
                   l.status AS lead_status
            FROM deals d
            JOIN leads l ON l.id = d.lead_id
            WHERE d.id = :did
        """),
        {"did": deal_id},
    ).fetchone()
    return dict(row._mapping) if row else None


def fetch_change_orders_pending(db, deal_id: int) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT id, co_number, reason, amount_delta, new_total,
                   requires_gate_c, status, created_at
            FROM change_orders
            WHERE deal_id = :did AND status = 'pending'
            ORDER BY co_number
        """),
        {"did": deal_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def record_gate_c_decision(
    db,
    deal_id: int,
    approved: bool,
    approved_by: str,
    reason: str | None = None,
) -> None:
    """Record the Gate C decision for scope + contractor approval."""
    from shared.state_machine import transition_deal  # avoid circular import

    if approved:
        db.execute(
            text("""
                UPDATE deals
                SET gate_c_approved_at = NOW(), gate_c_approved_by = :by
                WHERE id = :did
            """),
            {"by": approved_by, "did": deal_id},
        )
        # Mark the scope_of_work as approved
        db.execute(
            text("""
                UPDATE scope_of_work
                SET status = 'approved', approved_at = NOW(), approved_by = :by
                WHERE deal_id = :did AND status = 'pending_gate_c'
            """),
            {"by": approved_by, "did": deal_id},
        )
        transition_deal(db, deal_id, "rehab_active", actor=approved_by,
                        reason="Gate C approved — scope and contractor confirmed")
    else:
        transition_deal(db, deal_id, "scope_ready", actor=approved_by,
                        reason=f"Gate C rejected: {reason or 'no reason given'}")


def record_co_decision(
    db,
    co_id: int,
    approved: bool,
    approved_by: str,
    reason: str | None = None,
) -> None:
    """Record the Gate C decision on a change order."""
    status = "approved" if approved else "rejected"
    db.execute(
        text("""
            UPDATE change_orders
            SET status = :status,
                gate_c_approved_at = CASE WHEN :approved THEN NOW() ELSE NULL END,
                gate_c_approved_by = CASE WHEN :approved THEN :by ELSE NULL END,
                updated_at = NOW()
            WHERE id = :co_id
        """),
        {"status": status, "approved": approved, "by": approved_by, "co_id": co_id},
    )
    if approved:
        # Update deals.total_rehab_budget to new_total
        db.execute(
            text("""
                UPDATE deals
                SET total_rehab_budget = co.new_total
                FROM change_orders co
                WHERE co.id = :co_id AND deals.id = co.deal_id
            """),
            {"co_id": co_id},
        )
