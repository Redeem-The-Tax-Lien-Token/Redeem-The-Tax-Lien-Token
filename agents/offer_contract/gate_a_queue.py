"""
Gate A queue queries for Agent 8.

Returns leads waiting for Gate A approval (status = 'offer_ready' or
'strategy_switch') together with their latest deal record.

No business logic here — pure DB reads used by both the FastAPI endpoints
and the decision_memo builder.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def fetch_gate_a_queue(db: Session) -> list[dict[str, Any]]:
    """
    Return all leads in gate-A status with their latest deal.

    Rows are ordered oldest-first (longest-waiting first) so the operator
    sees the most urgent items at the top.
    """
    rows = db.execute(
        text("""
            SELECT
                l.id            AS lead_id,
                l.status        AS lead_status,
                l.address,
                l.city,
                l.state,
                l.zip,
                l.owner_name    AS seller_name,
                l.phone,
                l.updated_at    AS lead_updated_at,
                d.id            AS deal_id,
                d.strategy,
                d.offer_amount,
                d.arv_mid,
                d.arv_confidence,
                d.repair_estimate,
                d.mao,
                d.fallback_fee,
                d.fallback_flag,
                d.inspection_period_days,
                d.closing_date,
                d.emd_amount,
                d.underwriting_snapshot,
                d.gate_a_approved_at,
                d.updated_at    AS deal_updated_at
            FROM leads l
            LEFT JOIN LATERAL (
                SELECT * FROM deals
                WHERE lead_id = l.id
                ORDER BY created_at DESC
                LIMIT 1
            ) d ON true
            WHERE l.status IN ('offer_ready', 'strategy_switch')
              AND (l.manual_override_at IS NULL
                   OR l.manual_override_at < NOW() - INTERVAL '24 hours')
            ORDER BY l.updated_at ASC
        """)
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def fetch_gate_a_item(db: Session, lead_id: int) -> dict[str, Any] | None:
    """
    Return a single lead + deal record for the Gate A approval UI.
    Returns None if the lead is not in gate-A status.
    """
    queue = fetch_gate_a_queue(db)
    for item in queue:
        if item["lead_id"] == lead_id:
            return item
    return None


def record_gate_a_decision(
    db: Session,
    deal_id: int,
    approved: bool,
    approved_by: str,
    reason: str | None = None,
) -> None:
    """
    Write the Gate A decision.

    Approved:  sets gate_a_approved_at / gate_a_approved_by on the deal.
    Rejected:  caller is responsible for transitioning lead status to 'nurture'.
               This function only stamps the rejection reason in deals.notes.
    """
    now = datetime.utcnow()
    if approved:
        db.execute(
            text("""
                UPDATE deals
                SET gate_a_approved_at = :ts,
                    gate_a_approved_by  = :by,
                    updated_at          = :ts
                WHERE id = :deal_id
            """),
            {"ts": now, "by": approved_by, "deal_id": deal_id},
        )
    else:
        note_prefix = f"[Gate A rejected at {now.isoformat()} by {approved_by}]"
        if reason:
            note_prefix += f": {reason}"
        db.execute(
            text("""
                UPDATE deals
                SET notes = COALESCE(notes || E'\\n', '') || :note,
                    updated_at = :ts
                WHERE id = :deal_id
            """),
            {"note": note_prefix, "ts": now, "deal_id": deal_id},
        )
    db.commit()
