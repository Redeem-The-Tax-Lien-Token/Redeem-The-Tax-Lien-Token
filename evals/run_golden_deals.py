"""
Golden deal eval runner — wholesale track.

Loads every JSON file from evals/deals/, runs compute_wholesale() +
check_wholesale(), and asserts the result matches the expected values.

Usage:
    python evals/run_golden_deals.py            # prints pass/fail, exits 0/1
    python -m pytest tests/test_golden_deals_wholesale.py  # same via pytest

CI MUST fail if any golden deal's outcome changes without an approved
config change (§9 of CLAUDE.md).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from core.strategy.eligibility import check_wholesale
from core.strategy.offer_policy import select_offer_price
from core.strategy.wholesale import compute_wholesale

_DEALS_DIR = Path(__file__).parent / "deals"

_FLOAT_TOL = 0.01   # $0.01 tolerance for floating-point comparisons


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= _FLOAT_TOL


def run_deal(deal: dict, cfg: dict | None = None) -> list[str]:
    """
    Run one golden deal.  Returns a list of failure strings (empty = pass).
    """
    inp  = deal["inputs"]
    exp  = deal["expected"]
    did  = deal["id"]
    failures: list[str] = []

    case  = compute_wholesale(
        arv=float(inp["arv"]),
        repairs=float(inp["repairs"]),
        neighborhood_class=inp["neighborhood_class"],
        cfg=cfg,
    )
    elig  = check_wholesale(
        case=case,
        available_buyers=int(inp["available_buyers"]),
        seller_timeline_days=int(inp["seller_timeline_days"]),
        cfg=cfg,
    )
    offer = select_offer_price(case, elig, cfg=cfg)

    # ── Eligibility ───────────────────────────────────────────────────────────
    if elig.all_pass != exp["eligible"]:
        failures.append(
            f"{did}: eligible expected={exp['eligible']} got={elig.all_pass}  "
            f"reasons={elig.reasons}"
        )

    # ── Expected reasons (subset match — order-independent) ───────────────────
    exp_reasons = set(exp.get("reasons", []))
    got_reasons = set(elig.reasons)
    if exp_reasons != got_reasons:
        failures.append(
            f"{did}: reasons expected={sorted(exp_reasons)} got={sorted(got_reasons)}"
        )

    # ── Offer price (only checked for eligible deals) ─────────────────────────
    if "offer_price" in exp:
        expected_offer = float(exp["offer_price"])
        actual_offer   = offer if offer is not None else 0.0
        if not _close(actual_offer, expected_offer):
            failures.append(
                f"{did}: offer_price expected={expected_offer:,.0f} "
                f"got={actual_offer:,.0f}"
            )

    # ── Fee at offer ──────────────────────────────────────────────────────────
    if "fee_at_offer" in exp:
        if not _close(case.fee_at_offer, float(exp["fee_at_offer"])):
            failures.append(
                f"{did}: fee_at_offer expected={exp['fee_at_offer']:,.2f} "
                f"got={case.fee_at_offer:,.2f}"
            )

    return failures


def run_all(deals_dir: Path = _DEALS_DIR, cfg: dict | None = None) -> tuple[int, int, list[str]]:
    """
    Run all *.json deal files.
    Returns (passed, failed, all_failures).
    """
    deal_files = sorted(deals_dir.glob("*.json"))
    if not deal_files:
        raise FileNotFoundError(f"No deal files found in {deals_dir}")

    passed = 0
    failed = 0
    all_failures: list[str] = []

    for path in deal_files:
        deal = json.loads(path.read_text())
        failures = run_deal(deal, cfg=cfg)
        if failures:
            failed += 1
            all_failures.extend(failures)
            print(f"  FAIL  {deal['id']}")
            for f in failures:
                print(f"        {f}")
        else:
            passed += 1
            print(f"  PASS  {deal['id']}")

    return passed, failed, all_failures


if __name__ == "__main__":
    print(f"Running wholesale golden deals from {_DEALS_DIR}\n")
    passed, failed, _ = run_all()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
