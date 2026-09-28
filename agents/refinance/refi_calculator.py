"""
BRRRR refinance calculator — §2.3 implementation.

All math is deterministic Python.  The LLM is never used for any calculation.

The refi loan is sized to the LOWEST of:
  1. LTV cap: value_basis × refi_ltv
  2. DSCR cap: max loan supportable at min_dscr
  3. Cash-flow cap: max loan supportable at min_monthly_cash_flow

If any cap produces a loan < lender.min_loan → INELIGIBLE.

Public API:
    RefiResult          — dataclass with all intermediate values + final results
    calculate_refi(inputs, lender_profile, cfg=None) -> RefiResult
    pv_from_payment(payment, annual_rate, term_years) -> float
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

_INELIGIBLE = "INELIGIBLE"
BindingLiteral = Literal["LTV", "DSCR", "CASH_FLOW", "MIN_LOAN", "INELIGIBLE"]


@dataclass
class RefiResult:
    # Inputs (echoed for audit)
    arv:              float
    purchase:         float
    buy_closing:      float
    repair_estimate:  float
    rent_q:           float          # qualified rent (after haircut)
    months_owned:     int
    value_basis_rule: str

    # Computed intermediates
    cost_basis:       float
    value_basis:      float | str    # float or 'INELIGIBLE'
    opex_monthly:     float
    dscr_payment_max: float          # max P&I to hit min_dscr
    cf_payment_max:   float          # max P&I to hit min_cash_flow

    # Loan sizing
    loan_ltv:         float
    loan_dscr:        float
    loan_cf:          float
    refi_loan:        float | str    # float or 'INELIGIBLE'
    refi_binding:     BindingLiteral

    # Costs and outcomes
    refi_costs:       float
    refi_payment:     float          # monthly P&I on the refi loan
    cash_left_in:     float | str    # float or 'INELIGIBLE'
    cash_flow:        float
    dscr:             float
    cash_on_cash:     float | str    # float, 'INFINITE', or 'INELIGIBLE'

    eligible:         bool
    ineligibility_reasons: list[str] = field(default_factory=list)


def pv_from_payment(payment: float, annual_rate: float, term_years: int) -> float:
    """
    Present value of a fixed annuity (maximum loan for a given monthly payment).

    PV = P × [(1+r)^n - 1] / [r × (1+r)^n]
    where r = monthly rate, n = total months.

    Returns 0 if payment ≤ 0 or rate is 0.
    """
    if payment <= 0:
        return 0.0
    if annual_rate <= 0:
        return payment * term_years * 12   # degenerate; shouldn't happen

    r = annual_rate / 12
    n = term_years * 12
    factor = ((1 + r) ** n - 1) / (r * (1 + r) ** n)
    return round(payment * factor, 2)


def _monthly_payment(loan: float, annual_rate: float, term_years: int) -> float:
    """Standard fixed-rate monthly P&I payment."""
    if loan <= 0:
        return 0.0
    r = annual_rate / 12
    n = term_years * 12
    return round(loan * r * (1 + r) ** n / ((1 + r) ** n - 1), 2)


def calculate_refi(
    inputs: dict,
    lender_profile: dict,
    cfg: dict | None = None,
) -> RefiResult:
    """
    Calculate BRRRR refinance outcome per §2.3.

    inputs keys:
        arv              float  — after-repair value
        purchase         float  — acquisition price
        buy_closing      float  — closing costs on acquisition
        repair_estimate  float  — total rehab cost (actual or estimate)
        rent_actual      float  — in-place lease rent (or market rent)
        months_owned     int    — from seasoning check
        value_basis_rule str    — 'full_value' | 'cost_basis_value' | 'ineligible'
        all_in_cost      float  — optional; computed here if not provided

    lender_profile keys: refi_ltv, rate, term_years, cost_pct, fixed_fees,
                          min_loan, max_loan, rent_haircut

    cfg keys (from strategy.yaml brrrr/opex sections):
        min_dscr, min_monthly_cash_flow, opex.vacancy_rate,
        opex.maintenance_rate, opex.capex_rate, opex.management_rate,
        acquisition.tax_pct, acquisition.insurance_annual
    """
    cfg = cfg or {}
    brrrr_cfg = cfg.get("brrrr", {})
    opex_cfg  = cfg.get("opex", {})
    acq_cfg   = cfg.get("acquisition", {})

    arv             = float(inputs.get("arv") or 0)
    purchase        = float(inputs.get("purchase") or 0)
    buy_closing     = float(inputs.get("buy_closing") or 0)
    repair_estimate = float(inputs.get("repair_estimate") or 0)
    rent_raw        = float(inputs.get("rent_actual") or 0)
    months_owned    = int(inputs.get("months_owned") or 0)
    value_basis_rule = inputs.get("value_basis_rule", "ineligible")
    all_in_cost     = float(inputs.get("all_in_cost") or
                            (purchase + repair_estimate + buy_closing))

    # Lender params
    refi_ltv         = float(lender_profile.get("refi_ltv", 0.75))
    rate             = float(lender_profile.get("rate", 0.0725))
    term_years       = int(lender_profile.get("term_years", 30))
    cost_pct         = float(lender_profile.get("cost_pct", 0.025))
    fixed_fees       = float(lender_profile.get("fixed_fees", 2000))
    min_loan         = float(lender_profile.get("min_loan", 75_000))
    max_loan         = float(lender_profile.get("max_loan", 1_500_000))
    rent_haircut     = float(lender_profile.get("rent_haircut", 1.0))

    # Thresholds
    min_dscr     = float(brrrr_cfg.get("min_dscr", 1.25))
    min_cf       = float(brrrr_cfg.get("min_monthly_cash_flow", 200))

    # Operating expense rates
    vacancy_rate  = float(opex_cfg.get("vacancy_rate", 0.08))
    maint_rate    = float(opex_cfg.get("maintenance_rate", 0.08))
    capex_rate    = float(opex_cfg.get("capex_rate", 0.07))
    mgmt_rate     = float(opex_cfg.get("management_rate", 0.10))

    # Taxes and insurance (monthly)
    tax_pct           = float(acq_cfg.get("tax_pct", 0.02))
    insurance_annual  = float(acq_cfg.get("insurance_annual", 1600))
    taxes_monthly     = round(arv * tax_pct / 12, 2)
    insurance_monthly = round(insurance_annual / 12, 2)
    hoa_monthly       = 0.0   # no HOA by default; extend inputs if needed

    # Qualified rent (after lender haircut)
    rent_q = round(rent_raw * rent_haircut, 2)

    # Cost basis = purchase + buy_closing + documented repairs
    cost_basis = round(purchase + buy_closing + repair_estimate, 2)

    # Value basis
    ineligibility_reasons: list[str] = []

    if value_basis_rule == "full_value":
        value_basis: float | str = arv
    elif value_basis_rule == "cost_basis_value":
        value_basis = min(arv, cost_basis)
    else:
        value_basis = _INELIGIBLE
        ineligibility_reasons.append("Seasoning insufficient for any lender value basis")

    # Opex (monthly)
    opex_monthly = round(
        taxes_monthly
        + insurance_monthly
        + hoa_monthly
        + rent_q * (vacancy_rate + maint_rate + capex_rate + mgmt_rate),
        2,
    )

    # DSCR: rent_q / min_dscr = P&I + taxes + insurance + HOA
    fixed_monthly  = taxes_monthly + insurance_monthly + hoa_monthly
    dscr_payment_max = round(rent_q / min_dscr - fixed_monthly, 2)

    # Cash flow: rent_q - opex - min_cf = P&I
    cf_payment_max = round(rent_q - opex_monthly - min_cf, 2)

    if value_basis == _INELIGIBLE:
        refi_loan: float | str = _INELIGIBLE
        refi_binding: BindingLiteral = "INELIGIBLE"
        loan_ltv  = 0.0
        loan_dscr = 0.0
        loan_cf   = 0.0
    else:
        loan_ltv  = round(min(float(value_basis) * refi_ltv, max_loan), 2)
        loan_dscr = round(pv_from_payment(max(dscr_payment_max, 0), rate, term_years), 2)
        loan_cf   = round(pv_from_payment(max(cf_payment_max, 0), rate, term_years), 2)

        raw_loan = min(loan_ltv, loan_dscr, loan_cf)

        if raw_loan < min_loan:
            refi_loan    = _INELIGIBLE
            refi_binding = "MIN_LOAN"
            ineligibility_reasons.append(
                f"Refi loan ${raw_loan:,.0f} < lender minimum ${min_loan:,.0f}"
            )
        else:
            refi_loan    = raw_loan
            # Identify binding constraint
            if raw_loan == loan_ltv:
                refi_binding = "LTV"
            elif raw_loan == loan_dscr:
                refi_binding = "DSCR"
            else:
                refi_binding = "CASH_FLOW"

    # Refi costs
    refi_costs = round(
        (float(refi_loan) * cost_pct if refi_loan != _INELIGIBLE else 0)
        + fixed_fees,
        2,
    )

    # Refi monthly payment
    refi_payment = (
        _monthly_payment(float(refi_loan), rate, term_years)
        if refi_loan != _INELIGIBLE else 0.0
    )

    # Cash left in = all_in_cost + refi_costs − refi_loan
    if refi_loan == _INELIGIBLE:
        cash_left_in: float | str = _INELIGIBLE
        cash_flow    = 0.0
        dscr_val     = 0.0
        cash_on_cash: float | str = _INELIGIBLE
    else:
        cash_left_in = round(all_in_cost + refi_costs - float(refi_loan), 2)
        cash_flow    = round(rent_q - refi_payment - opex_monthly, 2)
        dscr_val     = round(rent_q / (refi_payment + fixed_monthly), 4) if (refi_payment + fixed_monthly) > 0 else 0.0
        if cash_left_in <= 0:
            cash_on_cash = "INFINITE"
        elif cash_left_in > 0:
            cash_on_cash = round((cash_flow * 12) / cash_left_in, 4)
        else:
            cash_on_cash = _INELIGIBLE

    eligible = (
        not ineligibility_reasons
        and refi_loan != _INELIGIBLE
        and value_basis != _INELIGIBLE
    )

    return RefiResult(
        arv=arv,
        purchase=purchase,
        buy_closing=buy_closing,
        repair_estimate=repair_estimate,
        rent_q=rent_q,
        months_owned=months_owned,
        value_basis_rule=value_basis_rule,
        cost_basis=cost_basis,
        value_basis=value_basis,
        opex_monthly=opex_monthly,
        dscr_payment_max=dscr_payment_max,
        cf_payment_max=cf_payment_max,
        loan_ltv=loan_ltv,
        loan_dscr=loan_dscr,
        loan_cf=loan_cf,
        refi_loan=refi_loan,
        refi_binding=refi_binding,
        refi_costs=refi_costs,
        refi_payment=refi_payment,
        cash_left_in=cash_left_in,
        cash_flow=cash_flow,
        dscr=dscr_val,
        cash_on_cash=cash_on_cash,
        eligible=eligible,
        ineligibility_reasons=ineligibility_reasons,
    )
