"""
import_csv.py — Bulk import leads from a CSV file into the leads table.

Usage:
  python import_csv.py --file leads.csv --segment "Q3-Indy-NE"
  python import_csv.py --file leads.csv --segment batch-1 --motivation pre_foreclosure --dry-run

Required env var: DATABASE_URL
"""

import argparse
import csv
import os
import sys
from pathlib import Path

_here      = Path(__file__).parent
_repo_root = _here.parent.parent
for _p in [str(_here), str(_repo_root)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared.db import session_ctx  # noqa: E402
from sqlalchemy import text         # noqa: E402

VALID_MOTIVATIONS = {"pre_foreclosure", "high_equity", "tax_delinquent", "vacant"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import leads from CSV into the CRM database.")
    p.add_argument("--file",         required=True,               help="Path to the CSV file")
    p.add_argument("--segment",      required=True,               help="Segment tag to apply to all rows")
    p.add_argument("--motivation",   default="tax_delinquent",    choices=sorted(VALID_MOTIVATIONS),
                   help="motivation_type for all imported rows (default: tax_delinquent)")
    p.add_argument("--owner-col",    default="Owner",             help="CSV column for owner name")
    p.add_argument("--address-col",  default="Property Address",  help="CSV column for street address")
    p.add_argument("--city-col",     default="City",              help="CSV column for city")
    p.add_argument("--state-col",    default="State",             help="CSV column for state")
    p.add_argument("--zip-col",      default="Zip",               help="CSV column for ZIP code")
    p.add_argument("--dry-run",      action="store_true",         help="Parse CSV and report counts without writing")
    return p.parse_args()


def normalise_zip(z: str) -> str:
    return z.strip().zfill(5) if z else ""


def main() -> None:
    args = parse_args()

    csv_path = Path(args.file)
    if not csv_path.exists():
        sys.exit(f"ERROR: file not found: {csv_path}")

    if not os.environ.get("DATABASE_URL"):
        sys.exit("ERROR: DATABASE_URL environment variable is not set.")

    rows_to_insert: list[dict] = []
    skipped_blank = 0

    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader, start=2):   # row 1 = header
            address = row.get(args.address_col, "").strip()
            if not address:
                skipped_blank += 1
                continue

            owner   = row.get(args.owner_col,  "").strip()
            city    = row.get(args.city_col,   "").strip() or "Indianapolis"
            state   = row.get(args.state_col,  "").strip() or "IN"
            zip_    = normalise_zip(row.get(args.zip_col, ""))

            rows_to_insert.append({
                "address":       address,
                "city":          city,
                "state":         state,
                "zip":           zip_,
                "owner_name":    owner or None,
                "motivation":    args.motivation,
                "segment":       args.segment,
                "list_source":   "csv_import",
            })

    if not rows_to_insert:
        print(f"No rows to import (skipped {skipped_blank} blank addresses). Exiting.")
        return

    if args.dry_run:
        print(f"[DRY RUN] Would attempt {len(rows_to_insert)} inserts "
              f"(skipped {skipped_blank} blank addresses). No DB writes performed.")
        return

    # Two-pass: load existing (address, owner_name) pairs for dedup, then insert fresh rows.
    inserted  = 0
    skipped_dup = 0

    with session_ctx() as db:
        # Fetch existing keys as a set for O(1) lookup — avoids N individual SELECTs
        existing = set(
            db.execute(
                text("SELECT LOWER(address), LOWER(COALESCE(owner_name,'')) FROM leads")
            ).fetchall()
        )

        for r in rows_to_insert:
            key = (r["address"].lower(), (r["owner_name"] or "").lower())
            if key in existing:
                skipped_dup += 1
                continue

            db.execute(
                text("""
                    INSERT INTO leads
                        (address, city, state, zip, owner_name,
                         motivation_type, segment, list_source, status)
                    VALUES
                        (:address, :city, :state, :zip, :owner_name,
                         :motivation, :segment, :list_source, 'new')
                """),
                r,
            )
            existing.add(key)   # prevent intra-batch duplicates
            inserted += 1

    print(
        f"Import complete — "
        f"inserted: {inserted}, "
        f"skipped duplicates: {skipped_dup}, "
        f"skipped blank addresses: {skipped_blank}"
    )


if __name__ == "__main__":
    main()
