"""
Portfolio performance calculations for Agent 16.

All math is deterministic Python.  Results are compared against the
underwriting pro forma snapshot stored at Gate A.

Public API:
    PerformanceMetrics     — dataclass
    compute_performance(deal, payments, maintenance_costs, cfg=None) -> PerformanceMetrics
    flag_underperformer(metrics, cfg=None) -> list[str]
    late_notice_due(payment) -> bool
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any


@dataclass
class PerformanceMetrics:
    deal_id:            int
    rent_pro_forma:     float   # from underwriting snapshot
    rent_actual:        float   # in-place lease rent
    rent_variance:      float   # actual - pro_forma

    cash_flow_actual:   float
    cash_flow_pro_forma: float
    cash_flow_variance: float

    dscr_actual:        float
    vacancy_months:     int     # months with $0 payment in period
    maintenance_ytd:    float   # actual maintenance spend year-to-date

    performance_flags:  list[str] = field(default_factory=list)


def compute_performance(
    deal: dict,
    payments: list[dict],    # rent_payments rows for the period
    maintenance_costs: list[dict],   # maintenance_requests with cost
    cfg: dict | None = None,
) -> PerformanceMetrics:
    """
    Compute monthly performance metrics vs. pro forma.

    deal keys: id, rent_actual, cash_flow_actual, dscr_actual,
               underwriting_snapshot (JSONB with rent_pro_forma, cash_flow)
    payments:  list of rent_payment rows with amount_due, amount_paid, status
    maintenance_costs: list with cost field
    """
    cfg      = cfg or {}
    snapshot = deal.get("underwriting_snapshot") or {}

    rent_actual     = float(deal.get("rent_actual") or 0)
    rent_pro_forma  = float(snapshot.get("market_rent") or snapshot.get("rent_actual") or rent_actual)
    rent_variance   = round(rent_actual - rent_pro_forma, 2)

    cf_actual     = float(deal.get("cash_flow_actual") or 0)
    cf_pro_forma  = float(snapshot.get("cash_flow") or 0)
    cf_variance   = round(cf_actual - cf_pro_forma, 2)

    dscr = float(deal.get("dscr_actual") or 0)

    vacancy_months = sum(
        1 for p in payments
        if (p.get("amount_paid") or 0) == 0 and p.get("status") in ("unpaid", "late", "pending")
    )

    maint_ytd = round(sum(float(m.get("cost") or 0) for m in maintenance_costs), 2)

    return PerformanceMetrics(
        deal_id=deal.get("id", 0),
        rent_pro_forma=rent_pro_forma,
        rent_actual=rent_actual,
        rent_variance=rent_variance,
        cash_flow_actual=cf_actual,
        cash_flow_pro_forma=cf_pro_forma,
        cash_flow_variance=cf_variance,
        dscr_actual=dscr,
        vacancy_months=vacancy_months,
        maintenance_ytd=maint_ytd,
    )


def flag_underperformer(metrics: PerformanceMetrics, cfg: dict | None = None) -> list[str]:
    """
    Return a list of flags if the property is underperforming thresholds.
    Empty list = no flags.
    """
    cfg        = cfg or {}
    brrrr_cfg  = cfg.get("brrrr", {})
    min_dscr   = float(brrrr_cfg.get("min_dscr", 1.25))
    min_cf     = float(brrrr_cfg.get("min_monthly_cash_flow", 200))

    flags: list[str] = []

    if metrics.dscr_actual > 0 and metrics.dscr_actual < min_dscr:
        flags.append(f"DSCR {metrics.dscr_actual:.2f} below minimum {min_dscr:.2f}")

    if metrics.cash_flow_actual < min_cf:
        flags.append(
            f"Cash flow ${metrics.cash_flow_actual:,.0f}/mo "
            f"below minimum ${min_cf:,.0f}/mo"
        )

    if metrics.rent_variance < -50:
        flags.append(
            f"Rent ${metrics.rent_actual:,.0f} is ${-metrics.rent_variance:,.0f} "
            f"below pro forma ${metrics.rent_pro_forma:,.0f}"
        )

    if metrics.vacancy_months >= 2:
        flags.append(f"{metrics.vacancy_months} months of vacancy in period")

    return flags


def late_notice_due(payment: dict, today: date | None = None) -> bool:
    """
    Return True if a late notice should be sent for this payment.

    Indiana IC 32-31-1-6: 10-day pay-or-quit notice. We trigger after the
    due date has passed (grace period is contractual, not statutory).
    A notice is due when status is 'pending' or 'late' and the due_date has passed.
    """
    today = today or datetime.now(tz=timezone.utc).date()
    if payment.get("status") not in ("pending", "late"):
        return False
    due = payment.get("due_date")
    if not due:
        return False
    if isinstance(due, str):
        due = date.fromisoformat(due)
    return due < today
