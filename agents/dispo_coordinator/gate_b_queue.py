"""
Gate B queue queries for Agent 11 (wholesale track only).

Gate B wholesale deals: status = 'buyer_selected'
  → operator approves assignment, EMD confirmation, assignment e-sign.

The BRRRR Gate B states ('clear_to_close_b', 'appraised') are handled by
Agents 12 and 15 respectively and are out of scope here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def fetch_gate_b_queue(db: Session) -> list[dict[str, Any]]:
    """
    Return all wholesale deals awaiting Gate B approval (status='buyer_selected'),
    joined with the lead and the selected buyer offer.

    Ordered oldest-first (longest-waiting first).
    """
    rows = db.execute(
        text("""
            SELECT
                d.id              AS deal_id,
                d.status          AS deal_status,
                d.strategy,
                d.offer_amount    AS psa_price,
                d.closing_date,
                d.assignment_fee_actual,
                d.emd_amount,
                d.selected_buyer_id,
                d.gate_b_approved_at,
                d.updated_at      AS deal_updated_at,
                l.id              AS lead_id,
                l.address,
                l.city,
                l.state,
                l.zip,
                l.owner_name      AS seller_name,
                b.id              AS buyer_id,
                b.name            AS buyer_name,
                b.phone           AS buyer_phone,
                b.email           AS buyer_email,
                bo.offer_amount   AS buyer_offer_amount,
                bo.pof_confirmed,
                bo.timeline_days,
                bo.emd_capacity,
                bo.ranked
            FROM deals d
            JOIN leads l ON l.id = d.lead_id
            LEFT JOIN buyers b ON b.id = d.selected_buyer_id
            LEFT JOIN buyer_offers bo
                   ON bo.deal_id = d.id AND bo.buyer_id = d.selected_buyer_id
            WHERE d.status = 'buyer_selected'
              AND d.strategy = 'wholesale'
            ORDER BY d.updated_at ASC
        """)
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def fetch_gate_b_item(db: Session, deal_id: int) -> dict[str, Any] | None:
    queue = fetch_gate_b_queue(db)
    for item in queue:
        if item["deal_id"] == deal_id:
            return item
    return None


def fetch_buyer_offers(db: Session, deal_id: int) -> list[dict[str, Any]]:
    """Return all buyer offers for a deal, ranked best-first."""
    rows = db.execute(
        text("""
            SELECT
                bo.id, bo.deal_id, bo.buyer_id, bo.offer_amount,
                bo.pof_confirmed, bo.timeline_days, bo.emd_capacity,
                bo.notes, bo.status, bo.ranked, bo.created_at,
                b.name AS buyer_name, b.phone AS buyer_phone, b.email AS buyer_email
            FROM buyer_offers bo
            JOIN buyers b ON b.id = bo.buyer_id
            WHERE bo.deal_id = :deal_id
            ORDER BY COALESCE(bo.ranked, 999), bo.offer_amount DESC
        """),
        {"deal_id": deal_id},
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def record_buyer_offer(
    db: Session,
    deal_id: int,
    buyer_id: int,
    offer_amount: float,
    pof_confirmed: bool = False,
    timeline_days: int | None = None,
    emd_capacity: float | None = None,
    notes: str | None = None,
) -> int:
    """Insert or update a buyer_offer row. Returns the row id."""
    row = db.execute(
        text("""
            INSERT INTO buyer_offers
                (deal_id, buyer_id, offer_amount, pof_confirmed,
                 timeline_days, emd_capacity, notes, status)
            VALUES
                (:deal_id, :buyer_id, :amount, :pof,
                 :timeline, :emd, :notes, 'pending')
            ON CONFLICT (deal_id, buyer_id) DO UPDATE SET
                offer_amount  = EXCLUDED.offer_amount,
                pof_confirmed = EXCLUDED.pof_confirmed,
                timeline_days = EXCLUDED.timeline_days,
                emd_capacity  = EXCLUDED.emd_capacity,
                notes         = EXCLUDED.notes,
                updated_at    = NOW()
            RETURNING id
        """),
        {
            "deal_id": deal_id, "buyer_id": buyer_id, "amount": offer_amount,
            "pof": pof_confirmed, "timeline": timeline_days,
            "emd": emd_capacity, "notes": notes,
        },
    ).fetchone()
    db.commit()
    return row[0]


def select_buyer(
    db: Session,
    deal_id: int,
    buyer_id: int,
    assignment_fee: float,
) -> None:
    """Mark a buyer as selected for assignment and update the deal."""
    db.execute(
        text("""
            UPDATE buyer_offers
            SET status = 'selected', updated_at = NOW()
            WHERE deal_id = :deal_id AND buyer_id = :buyer_id
        """),
        {"deal_id": deal_id, "buyer_id": buyer_id},
    )
    db.execute(
        text("""
            UPDATE deals
            SET selected_buyer_id     = :buyer_id,
                assignment_fee_actual = :fee,
                updated_at            = NOW()
            WHERE id = :deal_id
        """),
        {"buyer_id": buyer_id, "fee": assignment_fee, "deal_id": deal_id},
    )
    db.commit()


def record_gate_b_decision(
    db: Session,
    deal_id: int,
    approved: bool,
    approved_by: str,
    reason: str | None = None,
) -> None:
    now = datetime.utcnow()
    if approved:
        db.execute(
            text("""
                UPDATE deals
                SET gate_b_approved_at = :ts,
                    gate_b_approved_by  = :by,
                    updated_at          = :ts
                WHERE id = :deal_id
            """),
            {"ts": now, "by": approved_by, "deal_id": deal_id},
        )
    else:
        note = f"[Gate B rejected at {now.isoformat()} by {approved_by}]"
        if reason:
            note += f": {reason}"
        db.execute(
            text("""
                UPDATE deals
                SET notes = COALESCE(notes || E'\\n', '') || :note,
                    updated_at = :ts
                WHERE id = :deal_id
            """),
            {"note": note, "ts": now, "deal_id": deal_id},
        )
    db.commit()
