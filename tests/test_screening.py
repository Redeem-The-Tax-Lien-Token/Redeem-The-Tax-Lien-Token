"""
Unit tests for agents/leasing/screening.py.

Coverage:
  - evaluate_application: all criteria pass → approved
  - evaluate_application: income below minimum → denied
  - evaluate_application: credit score below minimum → denied
  - evaluate_application: DTI too high → denied
  - evaluate_application: eviction history → denied
  - evaluate_application: no prior landlord reference → denied
  - evaluate_application: credit_score not provided → pending
  - evaluate_application: multiple failures all listed
  - evaluate_application: raises on protected characteristic field
  - evaluate_application: criteria override works
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.leasing.screening import evaluate_application, CRITERIA_VERSION


def _app(**kw) -> dict:
    return {
        "monthly_income":          kw.get("monthly_income", 6000),
        "credit_score":            kw.get("credit_score", 650),
        "debt_to_income":          kw.get("debt_to_income", 0.30),
        "eviction_history":        kw.get("eviction_history", False),
        "prior_landlord_reference": kw.get("prior_landlord_reference", True),
    }


_CRITERIA = {
    "min_income_multiple_of_rent": 3.0,
    "min_credit_score": 580,
    "max_debt_to_income_pct": 0.45,
    "eviction_history_years": 7,
    "criminal_individualized_assessment": True,
    "prior_landlord_reference_required": True,
}


class TestEvaluateApplication:
    def test_all_pass_approved(self):
        result = evaluate_application(_app(), monthly_rent=1500, criteria=_CRITERIA)
        assert result.decision == "approved"
        assert result.reasons == []

    def test_income_below_minimum_denied(self):
        result = evaluate_application(_app(monthly_income=3000), monthly_rent=1500, criteria=_CRITERIA)
        # 3000 < 1500 × 3.0 = 4500
        assert result.decision == "denied"
        assert any("income" in r.lower() for r in result.reasons)

    def test_income_exactly_at_minimum_passes(self):
        result = evaluate_application(_app(monthly_income=4500), monthly_rent=1500, criteria=_CRITERIA)
        assert result.decision == "approved"

    def test_credit_score_below_min_denied(self):
        result = evaluate_application(_app(credit_score=500), monthly_rent=1500, criteria=_CRITERIA)
        assert result.decision == "denied"
        assert any("credit" in r.lower() for r in result.reasons)

    def test_dti_too_high_denied(self):
        result = evaluate_application(_app(debt_to_income=0.50), monthly_rent=1500, criteria=_CRITERIA)
        assert result.decision == "denied"
        assert any("debt-to-income" in r.lower() for r in result.reasons)

    def test_eviction_history_denied(self):
        result = evaluate_application(_app(eviction_history=True), monthly_rent=1500, criteria=_CRITERIA)
        assert result.decision == "denied"
        assert any("eviction" in r.lower() for r in result.reasons)

    def test_no_prior_reference_denied(self):
        result = evaluate_application(
            _app(prior_landlord_reference=False), monthly_rent=1500, criteria=_CRITERIA
        )
        assert result.decision == "denied"
        assert any("landlord reference" in r.lower() for r in result.reasons)

    def test_no_credit_score_pending(self):
        app = _app()
        app["credit_score"] = None
        result = evaluate_application(app, monthly_rent=1500, criteria=_CRITERIA)
        assert result.decision == "pending"

    def test_multiple_failures_all_listed(self):
        app = _app(monthly_income=1000, credit_score=400, eviction_history=True)
        result = evaluate_application(app, monthly_rent=1500, criteria=_CRITERIA)
        assert result.decision == "denied"
        assert len(result.reasons) >= 2

    def test_raises_on_protected_field(self):
        with pytest.raises(ValueError):
            evaluate_application({"race": "Asian", "monthly_income": 5000},
                                 monthly_rent=1500, criteria=_CRITERIA)

    def test_criteria_version_in_result(self):
        result = evaluate_application(_app(), monthly_rent=1500, criteria=_CRITERIA)
        assert result.criteria_version == CRITERIA_VERSION

    def test_criteria_override(self):
        strict = dict(_CRITERIA, min_credit_score=700)
        result = evaluate_application(_app(credit_score=650), monthly_rent=1500, criteria=strict)
        assert result.decision == "denied"
