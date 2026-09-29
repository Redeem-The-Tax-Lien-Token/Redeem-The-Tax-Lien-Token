"""
Golden deal eval runner — wholesale + BRRRR tracks.

Loads every JSON file from evals/deals/, runs the appropriate calculator,
and asserts the result matches the expected values.

deal_type values:
  "wholesale"  (default) — compute_wholesale + check_wholesale
  "brrrr"                — brrrr_max_price + check_brrrr (brrrr.enabled forced True)
  "combined"             — both tracks; checks strategy selection

Usage:
    python evals/run_golden_deals.py            # prints pass/fail, exits 0/1
    python -m pytest tests/test_golden_deals_wholesale.py  # same via pytest

CI MUST fail if any golden deal's outcome changes without an approved
config change (§9 of CLAUDE.md).
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from core.strategy.brrrr import INELIGIBLE, brrrr_max_price, compute_brrrr
from core.strategy.eligibility import check_brrrr, check_wholesale
from core.strategy.offer_policy import select_offer_price
from core.strategy.wholesale import compute_wholesale
from core.strategy import _lending

_DEALS_DIR = Path(__file__).parent / "deals"

_FLOAT_TOL = 0.01   # $0.01 tolerance for floating-point comparisons
_PCT_TOL   = 0.0001 # 0.01% tolerance for ratio/dscr comparisons


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= _FLOAT_TOL


def _pct_close(a: float, b: float) -> bool:
    return abs(a - b) <= _PCT_TOL


# ── Wholesale run ──────────────────────────────────────────────────────────────

def _run_wholesale(deal: dict, cfg: dict, lenders: dict) -> list[str]:
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

    if elig.all_pass != exp["eligible"]:
        failures.append(
            f"{did}: eligible expected={exp['eligible']} got={elig.all_pass}  "
            f"reasons={elig.reasons}"
        )

    exp_reasons = set(exp.get("reasons", []))
    got_reasons = set(elig.reasons)
    if exp_reasons != got_reasons:
        failures.append(
            f"{did}: reasons expected={sorted(exp_reasons)} got={sorted(got_reasons)}"
        )

    if "offer_price" in exp:
        expected_offer = float(exp["offer_price"])
        actual_offer   = offer if offer is not None else 0.0
        if not _close(actual_offer, expected_offer):
            failures.append(
                f"{did}: offer_price expected={expected_offer:,.0f} "
                f"got={actual_offer:,.0f}"
            )

    if "fee_at_offer" in exp:
        if not _close(case.fee_at_offer, float(exp["fee_at_offer"])):
            failures.append(
                f"{did}: fee_at_offer expected={exp['fee_at_offer']:,.2f} "
                f"got={case.fee_at_offer:,.2f}"
            )

    return failures


# ── BRRRR run ──────────────────────────────────────────────────────────────────

def _run_brrrr(deal: dict, cfg: dict, lenders: dict) -> list[str]:
    inp  = deal["inputs"]
    exp  = deal["expected"]
    did  = deal["id"]
    failures: list[str] = []

    repairs      = float(inp["repairs"])
    arv          = float(inp["arv"])
    market_rent  = float(inp["market_rent"])
    rehab_tier   = inp["rehab_tier"]
    avail_cap    = float(inp.get("available_capital", 999_999))
    active_proj  = int(inp.get("active_projects", 0))
    prop_type    = inp.get("property_type", "SFR")

    # Force brrrr.enabled=True for math validation — golden deals test the math,
    # not the feature flag (which stays False until capital is confirmed per §11).
    cfg_math = copy.deepcopy(cfg)
    cfg_math["brrrr"]["enabled"] = True

    mp = brrrr_max_price(
        repairs=repairs,
        arv=arv,
        market_rent=market_rent,
        rehab_tier=rehab_tier,
        cfg=cfg_math,
        lenders=lenders,
    )

    if "max_price" in exp:
        if not _close(mp, float(exp["max_price"])):
            failures.append(
                f"{did}: max_price expected={exp['max_price']:,.0f} got={mp:,.0f}"
            )

    # Check eligibility at max_price (or at inputs.purchase if provided)
    purchase = float(inp["purchase"]) if "purchase" in inp else mp
    if purchase <= 0:
        if exp.get("eligible", True) is False:
            return failures  # 0 max_price → ineligible, no case to check
        else:
            failures.append(f"{did}: max_price=0 but expected eligible=True")
            return failures

    case = compute_brrrr(
        purchase=purchase,
        repairs=repairs,
        arv=arv,
        market_rent=market_rent,
        rehab_tier=rehab_tier,
        cfg=cfg_math,
        lenders=lenders,
    )
    elig = check_brrrr(
        case=case,
        available_capital=avail_cap,
        active_projects=active_proj,
        property_type=prop_type,
        cfg=cfg_math,
        lenders=lenders,
    )

    expected_eligible = exp.get("eligible", True)
    if elig.all_pass != expected_eligible:
        failures.append(
            f"{did}: eligible expected={expected_eligible} got={elig.all_pass}  "
            f"reasons={elig.reasons}"
        )

    exp_reasons = set(exp.get("reasons", []))
    got_reasons = set(elig.reasons)
    if exp_reasons != got_reasons:
        failures.append(
            f"{did}: reasons expected={sorted(exp_reasons)} got={sorted(got_reasons)}"
        )

    if "cash_left_in_max" in exp and case.cash_left_in != INELIGIBLE:
        if case.cash_left_in > float(exp["cash_left_in_max"]):
            failures.append(
                f"{did}: cash_left_in={case.cash_left_in:,.2f} exceeds "
                f"max={exp['cash_left_in_max']:,.2f}"
            )

    if "dscr_min" in exp and isinstance(case.dscr, float):
        if case.dscr < float(exp["dscr_min"]) - _PCT_TOL:
            failures.append(
                f"{did}: dscr={case.dscr:.4f} below min={exp['dscr_min']}"
            )

    return failures


# ── Dispatcher ─────────────────────────────────────────────────────────────────

def run_deal(deal: dict, cfg: dict | None = None, lenders: dict | None = None) -> list[str]:
    """
    Run one golden deal.  Returns a list of failure strings (empty = pass).
    """
    from core.strategy import _config
    if cfg is None:
        cfg = _config.load()
    if lenders is None:
        lenders = _lending.load()

    deal_type = deal.get("deal_type", "wholesale")

    if deal_type == "wholesale":
        return _run_wholesale(deal, cfg, lenders)
    elif deal_type == "brrrr":
        return _run_brrrr(deal, cfg, lenders)
    else:
        return [f"{deal['id']}: unknown deal_type {deal_type!r}"]


def run_all(deals_dir: Path = _DEALS_DIR, cfg: dict | None = None, lenders: dict | None = None) -> tuple[int, int, list[str]]:
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
        failures = run_deal(deal, cfg=cfg, lenders=lenders)
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
    print(f"Running golden deals from {_DEALS_DIR}\n")
    passed, failed, _ = run_all()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
