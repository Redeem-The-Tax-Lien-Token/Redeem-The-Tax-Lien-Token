"""
Shared SQLAlchemy session management for all wholesale-brrrr-system agents.
Import this module in each agent's main.py or wherever DB access is needed.

Usage (FastAPI route):
    from shared.db import get_db
    @app.get("/lead/{lead_id}")
    def read_lead(lead_id: int, db: Session = Depends(get_db)):
        ...

Usage (background task / script):
    from shared.db import session_ctx
    with session_ctx() as db:
        db.execute(text("SELECT 1"))
"""

import json
import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,       # reconnect after idle / Replit sleep cycles
    pool_recycle=3600,        # recycle connections every hour
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


# ── FastAPI Depends() style ────────────────────────────────────────────────────

def get_db() -> Generator[Session, None, None]:
    """Yield a DB session; roll back on error, always close."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── Context manager style (scripts / background tasks) ────────────────────────

@contextmanager
def session_ctx() -> Generator[Session, None, None]:
    """Context manager wrapping get_db for non-FastAPI callers."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── Shared query helpers ───────────────────────────────────────────────────────

def get_lead(lead_id: int) -> dict | None:
    with session_ctx() as db:
        row = db.execute(
            text("SELECT * FROM leads WHERE id = :id"),
            {"id": lead_id},
        ).fetchone()
        return dict(row._mapping) if row else None


def update_lead_status(lead_id: int, status: str) -> bool:
    with session_ctx() as db:
        result = db.execute(
            text("UPDATE leads SET status = :status WHERE id = :id"),
            {"status": status, "id": lead_id},
        )
        return result.rowcount > 0


def log_agent_event(
    source_agent: str,
    target_agent: str,
    payload: dict,
    response: dict,
    status_code: int,
) -> None:
    """Write one row to agent_events for every inter-agent HTTP call."""
    with session_ctx() as db:
        db.execute(
            text("""
                INSERT INTO agent_events
                    (source_agent, target_agent, payload, response, status_code)
                VALUES
                    (:source, :target, :payload::jsonb, :response::jsonb, :status_code)
            """),
            {
                "source": source_agent,
                "target": target_agent,
                "payload": json.dumps(payload),
                "response": json.dumps(response),
                "status_code": status_code,
            },
        )
