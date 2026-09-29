"""
Capital pool read/update helpers for Agent 0 (Orchestrator).

The capital_pool table has a single row tracking available, committed,
and trapped capital across all active projects.  Agent 0 reads it to
include in the daily brief and to gate BRRRR eligibility checks.

Table schema (from shared/schema/migrate.sql):
  id               SERIAL PRIMARY KEY
  available        NUMERIC(14, 2)
  committed        NUMERIC(14, 2)
  trapped_in_brrrr NUMERIC(14, 2)
  expected_return_at TIMESTAMPTZ
  updated_at       TIMESTAMPTZ
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class CapitalPool:
    available:          float
    committed:          float
    trapped_in_brrrr:   float
    expected_return_at: datetime | None
    updated_at:         datetime | None

    @property
    def total_deployed(self) -> float:
        return self.committed + self.trapped_in_brrrr

    @property
    def summary(self) -> dict:
        return {
            "available":          round(self.available, 2),
            "committed":          round(self.committed, 2),
            "trapped_in_brrrr":   round(self.trapped_in_brrrr, 2),
            "total_deployed":     round(self.total_deployed, 2),
            "expected_return_at": self.expected_return_at.isoformat() if self.expected_return_at else None,
        }


_EMPTY_POOL = CapitalPool(
    available=0.0,
    committed=0.0,
    trapped_in_brrrr=0.0,
    expected_return_at=None,
    updated_at=None,
)


def read_capital_pool(db: Session) -> CapitalPool:
    """
    Read the single capital_pool row.
    Returns an empty pool if no row exists yet.
    """
    row = db.execute(
        text("""
            SELECT available, committed, trapped_in_brrrr,
                   expected_return_at, updated_at
            FROM capital_pool
            LIMIT 1
        """)
    ).fetchone()

    if row is None:
        return _EMPTY_POOL

    m = row._mapping
    return CapitalPool(
        available=float(m["available"] or 0),
        committed=float(m["committed"] or 0),
        trapped_in_brrrr=float(m["trapped_in_brrrr"] or 0),
        expected_return_at=m.get("expected_return_at"),
        updated_at=m.get("updated_at"),
    )


def update_capital_pool(
    db: Session,
    *,
    available: float,
    committed: float,
    trapped_in_brrrr: float,
    expected_return_at: datetime | None = None,
) -> None:
    """
    Upsert the capital_pool row (INSERT ... ON CONFLICT DO UPDATE).
    The table always has at most one row (id=1 convention).
    """
    db.execute(
        text("""
            INSERT INTO capital_pool
                (id, available, committed, trapped_in_brrrr, expected_return_at, updated_at)
            VALUES
                (1, :available, :committed, :trapped, :expected_return_at, NOW())
            ON CONFLICT (id) DO UPDATE SET
                available          = EXCLUDED.available,
                committed          = EXCLUDED.committed,
                trapped_in_brrrr   = EXCLUDED.trapped_in_brrrr,
                expected_return_at = EXCLUDED.expected_return_at,
                updated_at         = NOW()
        """),
        {
            "available":          available,
            "committed":          committed,
            "trapped":            trapped_in_brrrr,
            "expected_return_at": expected_return_at,
        },
    )
