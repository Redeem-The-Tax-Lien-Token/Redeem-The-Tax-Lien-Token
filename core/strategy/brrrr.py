"""
BRRRR deal calculator — §2.3 (v2.1).

compute_brrrr() evaluates a single purchase price.
brrrr_max_price() finds the highest purchase price that passes all gates
via monotonic bisection ($100 precision, rounded down to $1,000).

All inputs come from config/strategy.yaml and config/lending.yaml.
Nothing is hard-coded here — every rate and threshold is passed via cfg/lenders.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from . import _config
from . import _lending

INELIGIBLE = "INELIGIBLE"

RehabTier = Literal["LIGHT", "MEDIUM", "HEAVY", "GUT"]
ValueBasis = float | Literal["INELIGIBLE"]
RefiLoan   = float | Literal["INELIGIBLE"]


def _pv_annuity(monthly_payment: float, annual_rate: float, term_years: int) -> float:
    """Maximum loan principal that supports monthly_payment at annual_rate over term_years."""
    r = annual_rate / 12
    n = term_years * 12
    if r == 0:
        return monthly_payment * n
    return monthly_payment * (1 - (1 + r) ** -n) / r


@dataclass(frozen=True)
class BrrrrCase:
    # Inputs
    purchase:          float
    repairs:           float
    arv:               float
    market_rent:       float
    rehab_tier:        str
    # Timeline
    work_months:       float
    months_owned:      float
    months_to_refi:    float
    # Acquisition costs
    buy_closing:       float
    acq_loan:          float
    financing_costs:   float
    holding:           float
    contingency:       float
    all_in_cost:       float
    peak_capital:      float
    # Refi
    value_basis:       object   # float or "INELIGIBLE"
    refi_loan:         object   # float or "INELIGIBLE"
    refi_binding:      str      # "LTV" | "DSCR" | "CASH_FLOW" | "INELIGIBLE" | "MIN_LOAN"
    refi_costs:        float
    cash_left_in:      float
    # Operating
    rent_q:            float    # haircut-adjusted rent
    refi_payment:      float
    opex:              float
    cash_flow:         float
    dscr:              float
    # Feasibility flags
    eligible:          bool


def compute_brrrr(
    purchase:     float,
    repairs:      float,
    arv:          float,
    market_rent:  float,
    rehab_tier:   str,
    cfg:          dict | None = None,
    lenders:      dict | None = None,
    acq_id:       str = "hm_default",
    refi_id:      str = "dscr_default",
) -> BrrrrCase:
    """
    Evaluate the BRRRR math for a given purchase price (§2.3).

    Args:
        purchase:    Proposed purchase price.
        repairs:     Estimated repair cost.
        arv:         After-repair value.
        market_rent: Gross monthly market rent.
        rehab_tier:  "LIGHT" | "MEDIUM" | "HEAVY" | "GUT"
        cfg:         strategy.yaml dict (loaded from disk if None).
        lenders:     lending.yaml dict (loaded from disk if None).
        acq_id:      Acquisition lender profile id.
        refi_id:     Refi lender profile id.

    Returns:
        BrrrrCase with all intermediate values and eligible flag.
    """
    if cfg is None:
        cfg = _config.load()
    if lenders is None:
        lenders = _lending.load()

    tier = rehab_tier.upper()
    acq  = _lending.get_acq_lender(lenders, acq_id)
    refi = _lending.get_refi_lender(lenders, refi_id)
    acfg = cfg["acquisition"]
    ocfg = cfg["opex"]
    bcfg = cfg["brrrr"]

    # ── Timeline ──────────────────────────────────────────────────────────────
    rehab_months   = float(acfg["rehab_months"][tier])
    leaseup_months = float(acfg["leaseup_months"])
    work_months    = rehab_months + leaseup_months
    months_owned   = max(work_months, float(refi["full_value_seasoning_months"]))
    months_to_refi = months_owned + float(refi["refi_process_months"])

    # ── Acquisition costs ─────────────────────────────────────────────────────
    buy_closing_pct = float(acfg["buy_closing_pct"])
    buy_closing     = purchase * buy_closing_pct
    acq_loan        = purchase * float(acq["ltc_purchase"]) + repairs * float(acq["ltc_rehab"])
    financing_costs = acq_loan * float(acq["points"]) + float(acq["fixed_fees"])

    # avg balance ≈ acq_loan (conservative; draw schedules not modeled here)
    taxes_monthly     = arv * float(acfg["tax_pct"]) / 12
    insurance_monthly = float(acfg["insurance_annual"]) / 12
    utilities_monthly = float(acfg["utilities_monthly"])
    holding = months_to_refi * (
        acq_loan * float(acq["rate"]) / 12
        + taxes_monthly
        + insurance_monthly
        + utilities_monthly
    )

    contingency_pct = float(acfg["contingency_pct"][tier])
    contingency     = repairs * contingency_pct

    all_in_cost = purchase + repairs + buy_closing + holding + financing_costs + contingency
    peak_capital = all_in_cost - acq_loan

    # ── Refi value basis ──────────────────────────────────────────────────────
    cost_basis = purchase + buy_closing + repairs
    full_season = float(refi["full_value_seasoning_months"])
    min_season  = float(refi["min_seasoning_months"])
    early_rule  = refi.get("early_rule", "cost_basis_value")

    if months_owned >= full_season:
        value_basis: ValueBasis = arv
    elif months_owned >= min_season and early_rule == "cost_basis_value":
        value_basis = min(arv, cost_basis)
    else:
        value_basis = INELIGIBLE

    # ── Refi loan sizing ──────────────────────────────────────────────────────
    refi_rate   = float(refi["rate"])
    refi_term   = int(refi["term_years"])
    rent_haircut = float(refi.get("rent_haircut", 1.0))
    rent_q      = market_rent * rent_haircut

    min_dscr    = float(bcfg["min_dscr"])
    min_cf      = float(bcfg["min_monthly_cash_flow"])

    # opex excluding debt service
    opex_rate   = (
        float(ocfg["vacancy_rate"])
        + float(ocfg["maintenance_rate"])
        + float(ocfg["capex_rate"])
        + float(ocfg["management_rate"])
    )
    opex_nodbt  = taxes_monthly + insurance_monthly + rent_q * opex_rate

    if value_basis == INELIGIBLE:
        refi_loan: RefiLoan = INELIGIBLE
        refi_binding = INELIGIBLE
    else:
        loan_ltv  = min(float(value_basis) * float(refi["refi_ltv"]), float(refi["max_loan"]))

        # DSCR constraint: rent_q = monthly_pmt * min_dscr + taxes + insurance
        # → max pmt = (rent_q - taxes - insurance) / min_dscr
        dscr_max_pmt = (rent_q - taxes_monthly - insurance_monthly) / min_dscr
        loan_dscr    = _pv_annuity(max(dscr_max_pmt, 0.0), refi_rate, refi_term)

        # Cash-flow constraint: rent_q - pmt - opex_nodbt >= min_cf
        # → max pmt = rent_q - opex_nodbt - min_cf
        cf_max_pmt   = rent_q - opex_nodbt - min_cf
        loan_cf      = _pv_annuity(max(cf_max_pmt, 0.0), refi_rate, refi_term)

        raw_loan = min(loan_ltv, loan_dscr, loan_cf)

        if raw_loan < float(refi["min_loan"]):
            refi_loan    = INELIGIBLE
            refi_binding = "MIN_LOAN"
        else:
            refi_loan = raw_loan
            if raw_loan == loan_ltv:
                refi_binding = "LTV"
            elif raw_loan == loan_dscr:
                refi_binding = "DSCR"
            else:
                refi_binding = "CASH_FLOW"

    # ── Results ───────────────────────────────────────────────────────────────
    if refi_loan == INELIGIBLE:
        refi_costs   = 0.0
        cash_left_in = math.inf
        refi_payment = 0.0
        opex_val     = 0.0
        cash_flow    = -math.inf
        dscr_val     = 0.0
        eligible     = False
    else:
        refi_costs    = float(refi_loan) * float(refi["cost_pct"]) + float(refi["fixed_fees"])
        cash_left_in  = all_in_cost + refi_costs - float(refi_loan)
        r             = refi_rate / 12
        n             = refi_term * 12
        refi_payment  = float(refi_loan) * r / (1 - (1 + r) ** -n)
        opex_val      = opex_nodbt + rent_q * (
            float(ocfg["vacancy_rate"])
            + float(ocfg["maintenance_rate"])
            + float(ocfg["capex_rate"])
            + float(ocfg["management_rate"])
        )
        # opex_val already includes taxes+insurance in opex_nodbt plus the rate-based items
        # recalculate cleanly: total monthly outflow minus debt service
        opex_val      = taxes_monthly + insurance_monthly + rent_q * opex_rate
        cash_flow     = rent_q - refi_payment - opex_val
        dscr_denom    = refi_payment + taxes_monthly + insurance_monthly
        dscr_val      = rent_q / dscr_denom if dscr_denom > 0 else 0.0
        eligible      = True

    return BrrrrCase(
        purchase=purchase,
        repairs=repairs,
        arv=arv,
        market_rent=market_rent,
        rehab_tier=tier,
        work_months=work_months,
        months_owned=months_owned,
        months_to_refi=months_to_refi,
        buy_closing=buy_closing,
        acq_loan=acq_loan,
        financing_costs=financing_costs,
        holding=holding,
        contingency=contingency,
        all_in_cost=all_in_cost,
        peak_capital=peak_capital,
        value_basis=value_basis,
        refi_loan=refi_loan,
        refi_binding=refi_binding,
        refi_costs=refi_costs,
        cash_left_in=cash_left_in,
        rent_q=rent_q,
        refi_payment=refi_payment,
        opex=opex_val,
        cash_flow=cash_flow,
        dscr=dscr_val,
        eligible=eligible,
    )


def brrrr_max_price(
    repairs:     float,
    arv:         float,
    market_rent: float,
    rehab_tier:  str,
    cfg:         dict | None = None,
    lenders:     dict | None = None,
    acq_id:      str = "hm_default",
    refi_id:     str = "dscr_default",
) -> float:
    """
    Highest purchase price (rounded down to $1,000) at which the BRRRR deal
    passes all numeric gates and cash_left_in <= target_cash_left_in.

    Uses monotonic bisection at $100 precision.
    Returns 0.0 if no valid price exists up to ARV.
    """
    if cfg is None:
        cfg = _config.load()
    if lenders is None:
        lenders = _lending.load()

    bcfg   = cfg["brrrr"]
    target = float(bcfg["target_cash_left_in"])

    def _passes(price: float) -> bool:
        case = compute_brrrr(
            purchase=price,
            repairs=repairs,
            arv=arv,
            market_rent=market_rent,
            rehab_tier=rehab_tier,
            cfg=cfg,
            lenders=lenders,
            acq_id=acq_id,
            refi_id=refi_id,
        )
        if not case.eligible:
            return False
        min_dscr    = float(bcfg["min_dscr"])
        min_cf      = float(bcfg["min_monthly_cash_flow"])
        max_cli     = float(bcfg["max_cash_left_in"])
        min_rr      = float(bcfg["min_rent_ratio"])
        if case.cash_left_in > target:
            return False
        if case.cash_left_in > max_cli:
            return False
        if case.dscr < min_dscr:
            return False
        if case.cash_flow < min_cf:
            return False
        if case.all_in_cost > 0 and market_rent / case.all_in_cost < min_rr:
            return False
        return True

    # Quick check: does any price work up to ARV?
    if not _passes(1_000.0):
        return 0.0

    # Bisect between $1k and ARV at $100 granularity
    lo = 1_000.0
    hi = arv

    # Make sure hi is a candidate
    while hi > lo and not _passes(lo):
        return 0.0

    # Find highest passing price via bisection
    # _passes is monotonically decreasing (higher price = more capital needed)
    precision = 100.0
    while hi - lo > precision:
        mid = (lo + hi) / 2
        mid = round(mid / precision) * precision
        if _passes(mid):
            lo = mid
        else:
            hi = mid - precision

    # Final answer: round down to nearest $1,000
    best = math.floor(lo / 1_000) * 1_000
    if best < 1_000 or not _passes(best):
        return 0.0
    return float(best)
