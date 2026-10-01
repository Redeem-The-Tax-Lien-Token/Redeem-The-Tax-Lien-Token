#!/usr/bin/env python3
"""
Outreach canary dry-run — no DB, no Twilio, no external APIs required.

Exercises the full compliance pipeline for 25 sample Indianapolis leads:
  build_message() → build_seller_sms() → check_outbound()

Prints each message, its segment count, and disclosure presence.
Fails loudly if any message would be blocked by the compliance linter.

Run from the repo root:
    python scripts/canary_dry_run.py
"""

import sys
from pathlib import Path

_repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(_repo_root))
sys.path.insert(0, str(_repo_root / "agents" / "outreach"))

from shared.compliance.disclosures import build_seller_sms, INDIANA_HB1068_DISCLOSURE
from shared.compliance.linter import check_outbound

# ── Sample lead roster (25 Indianapolis properties, no real PII) ──────────────

SAMPLE_LEADS = [
    {"id": i, "owner_name": name, "address": addr, "city": "Indianapolis", "phone": f"+1317555{i:04d}"}
    for i, (name, addr) in enumerate([
        ("J. Smith",     "1842 N Delaware St"),
        ("M. Johnson",   "3217 E 10th St"),
        (None,           "4801 N Illinois St"),   # unknown owner → "there"
        ("T. Williams",  "2109 S Meridian St"),
        ("R. Brown",     "635 W 38th St"),
        ("L. Jones",     "1120 E 16th St"),
        ("P. Garcia",    "2540 N College Ave"),
        ("K. Davis",     "901 S Tibbs Ave"),
        ("C. Miller",    "4405 E Washington St"),
        ("A. Wilson",    "750 N Rural St"),
        ("D. Moore",     "3100 W 30th St"),
        ("N. Taylor",    "1700 S Holt Rd"),
        ("B. Anderson",  "5202 N Keystone Ave"),
        ("V. Thomas",    "2801 E 46th St"),
        ("E. Jackson",   "1400 S Emerson Ave"),
        ("F. White",     "3600 N Arlington Ave"),
        ("G. Harris",    "820 E 25th St"),
        ("H. Martin",    "4100 W Michigan St"),
        ("I. Thompson",  "2230 N Shadeland Ave"),
        ("J. Martinez",  "1510 S Harding St"),
        ("K. Robinson",  "600 N Tuxedo St"),
        ("L. Clark",     "3800 E 10th St"),
        ("M. Rodriguez", "1950 N Gray St"),
        (None,           "4400 S Meridian St"),   # unknown owner → "there"
        ("O. Lewis",     "2700 E 38th St"),
    ], start=1)
]

# ── SMS body builder (mirrors agents/outreach/twilio_utils.py) ────────────────

SMS_TEMPLATES = {
    1: (
        "Hi {owner_name}, I'm a local cash buyer interested in your property "
        "at {address}, {city}. No repairs, no agent fees, fast close. "
        "Would you consider a cash offer?"
    ),
    2: (
        "Hi {owner_name}, following up on {address}. I can close in as little "
        "as 2 weeks — no repairs or showings needed. Still open to a cash offer?"
    ),
    3: (
        "Last message about {address}. If the timing isn't right, no worries — "
        "I understand. If you ever want a cash offer, feel free to reach out."
    ),
}


def build_message(touch_number: int, owner_name, address: str, city: str) -> str:
    name = owner_name or "there"
    template = SMS_TEMPLATES[touch_number]
    return template.format(owner_name=name, address=address, city=city)


# ── Run ───────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*70}")
    print(f"  REDEEM Outreach Canary Dry-Run — {len(SAMPLE_LEADS)} leads × 3 touches")
    print(f"{'='*70}\n")

    # Verify disclosure text is non-empty before iterating
    assert "assign" in INDIANA_HB1068_DISCLOSURE.lower(), \
        "FATAL: HB 1068 disclosure text is missing 'assign' — check compliance/templates/"
    assert "investor" in INDIANA_HB1068_DISCLOSURE.lower(), \
        "FATAL: HB 1068 disclosure text is missing 'investor'"
    print(f"  Disclosure loaded: {len(INDIANA_HB1068_DISCLOSURE)} chars\n")

    total     = 0
    blocked   = 0
    failures  = []

    for touch in (1, 2, 3):
        print(f"── Touch {touch} {'─'*60}")
        for lead in SAMPLE_LEADS:
            body = build_message(
                touch_number = touch,
                owner_name   = lead["owner_name"],
                address      = lead["address"],
                city         = lead["city"],
            )
            full_message, disclosure_version = build_seller_sms(body)
            lint = check_outbound(full_message, disclosure_version)
            total += 1

            status = "OK" if lint.ok else "BLOCKED"
            if not lint.ok:
                blocked += 1
                failures.append({
                    "touch": touch, "lead_id": lead["id"],
                    "address": lead["address"], "reason": lint.reason,
                    "message": full_message,
                })

            # Show first lead of each touch in full; the rest as one-liners
            if lead["id"] == 1:
                print(f"\n  [Sample message — touch {touch}, lead {lead['id']}]")
                print(f"  To: {lead['phone']}")
                print(f"  Segments: {lint.segment_count}")
                print(f"  Disclosure version: {disclosure_version}")
                print(f"  Body ({len(full_message)} chars):\n")
                for line in full_message.split("\n"):
                    print(f"    {line}")
                print()
            else:
                indicator = "✓" if lint.ok else "✗"
                print(
                    f"  {indicator} lead={lead['id']:3d}  "
                    f"seg={lint.segment_count}  "
                    f"chars={len(full_message):4d}  "
                    f"{status}"
                    + (f"  [{lint.reason}]" if not lint.ok else "")
                )

        print()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"{'='*70}")
    print(f"  Results: {total} messages checked, {total - blocked} OK, {blocked} BLOCKED")
    print(f"{'='*70}\n")

    if failures:
        print("  FAILURES:")
        for f in failures:
            print(f"    touch={f['touch']} lead={f['lead_id']} addr={f['address']}")
            print(f"    reason: {f['reason']}")
            print(f"    message: {f['message'][:120]}…\n")
        sys.exit(1)

    print("  All messages passed compliance lint. Safe to deploy to Replit.\n")
    print("  Next step: run the migration on Replit, then hit POST /run?dry_run=true\n")


if __name__ == "__main__":
    main()
