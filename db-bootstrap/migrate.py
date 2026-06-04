#!/usr/bin/env python3
"""
One-shot migration runner for the wholesale-brrrr-system database.
Run this ONCE after provisioning Replit PostgreSQL to create all 6 tables.

Usage:
    DATABASE_URL=postgresql://user:pass@host:5432/dbname python db-bootstrap/migrate.py

The script is idempotent — CREATE TABLE IF NOT EXISTS means re-running it is safe.
"""

import os
import sys
from pathlib import Path

try:
    from sqlalchemy import create_engine, inspect, text
except ImportError:
    sys.exit("ERROR: sqlalchemy not installed.\nRun: pip install sqlalchemy psycopg2-binary")

EXPECTED_TABLES = frozenset({
    "leads",
    "outreach_log",
    "lead_scores",
    "deals",
    "buyers",
    "agent_events",
})

# Resolve path relative to this script so it works from any working directory.
MIGRATION_SQL = Path(__file__).parent / "migrate.sql"


def main() -> None:
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        sys.exit("ERROR: DATABASE_URL environment variable is not set.")

    if not MIGRATION_SQL.exists():
        sys.exit(
            f"ERROR: Migration file not found at {MIGRATION_SQL}\n"
            "Make sure the symlink db-bootstrap/migrate.sql -> "
            "../shared/schema/migrate.sql exists."
        )

    print(f"[1/3] Connecting to database …")
    engine = create_engine(db_url, pool_pre_ping=True)

    sql = MIGRATION_SQL.read_text(encoding="utf-8")

    print("[2/3] Executing migration SQL …")
    with engine.connect() as conn:
        conn.execute(text(sql))
        conn.commit()
    print("      Migration SQL executed successfully.")

    print("[3/3] Verifying tables …")
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())

    found   = EXPECTED_TABLES & existing
    missing = EXPECTED_TABLES - existing

    for table in sorted(found):
        print(f"      [OK]      {table}")
    for table in sorted(missing):
        print(f"      [MISSING] {table}", file=sys.stderr)

    if missing:
        sys.exit(
            f"\nERROR: {len(missing)} table(s) not found after migration: "
            + ", ".join(sorted(missing))
        )

    print(f"\nAll {len(EXPECTED_TABLES)} tables verified. Database is ready.\n")


if __name__ == "__main__":
    main()
