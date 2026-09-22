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

Usage (agent run observability — §4 principle 9):
    from shared.db import agent_run_ctx
    with agent_run_ctx("outreach", inputs_hash="abc123") as run:
        ... do work ...
        run["tokens_used"] = 412
        run["outputs"] = {"sent": 3}
    # finish_agent_run() is called automatically on exit (even on error)
"""

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

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


def start_agent_run(
    agent_name: str,
    inputs_hash: str | None = None,
) -> int:
    """
    Insert a started agent_runs row and return its id.
    Call finish_agent_run() when the run ends, or use agent_run_ctx().
    """
    with session_ctx() as db:
        row = db.execute(
            text("""
                INSERT INTO agent_runs (agent_name, inputs_hash, started_at)
                VALUES (:name, :hash, NOW())
                RETURNING id
            """),
            {"name": agent_name, "hash": inputs_hash},
        ).fetchone()
        return row.id  # type: ignore[union-attr]


def finish_agent_run(
    run_id: int,
    *,
    outputs: dict | None = None,
    tokens_used: int | None = None,
    cost_usd: float | None = None,
    error: str | None = None,
) -> None:
    """Update an agent_runs row with results on completion or error."""
    with session_ctx() as db:
        db.execute(
            text("""
                UPDATE agent_runs
                SET finished_at = NOW(),
                    outputs      = :outputs::jsonb,
                    tokens_used  = :tokens,
                    cost_usd     = :cost,
                    error        = :error
                WHERE id = :run_id
            """),
            {
                "run_id":  run_id,
                "outputs": json.dumps(outputs) if outputs is not None else None,
                "tokens":  tokens_used,
                "cost":    cost_usd,
                "error":   error,
            },
        )


@contextmanager
def agent_run_ctx(
    agent_name: str,
    inputs: Any = None,
) -> Generator[dict, None, None]:
    """
    Context manager that wraps a unit of agent work with an agent_runs row.

    Yields a mutable dict that callers can populate:
        run["tokens_used"] = 412
        run["outputs"]     = {"sent": 3}

    finish_agent_run() is called on __exit__ whether or not an exception is raised.
    On exception, run["error"] is set to the repr of the exception.

    inputs can be any JSON-serialisable value; its SHA-256 is stored as inputs_hash.
    """
    inputs_hash: str | None = None
    if inputs is not None:
        raw = json.dumps(inputs, sort_keys=True, default=str)
        inputs_hash = hashlib.sha256(raw.encode()).hexdigest()

    run_id = start_agent_run(agent_name, inputs_hash=inputs_hash)
    run: dict = {"run_id": run_id}
    try:
        yield run
    except Exception as exc:
        run.setdefault("error", repr(exc))
        raise
    finally:
        finish_agent_run(
            run_id,
            outputs=run.get("outputs"),
            tokens_used=run.get("tokens_used"),
            cost_usd=run.get("cost_usd"),
            error=run.get("error"),
        )


def inputs_hash(payload: Any) -> str:
    """SHA-256 of a JSON-serialised payload — for idempotency and dedup."""
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


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
