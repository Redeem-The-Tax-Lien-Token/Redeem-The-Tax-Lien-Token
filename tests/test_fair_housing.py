"""
Unit tests for agents/leasing/fair_housing.py.

Coverage:
  - lint_listing: clean text passes
  - lint_listing: "no children" fails
  - lint_listing: "adults only" fails
  - lint_listing: "no section 8" fails (source of income)
  - lint_listing: "no housing vouchers" fails
  - lint_listing: "males only" fails
  - lint_listing: multiple violations all reported
  - guard_application_fields: clean dict passes
  - guard_application_fields: 'race' key raises ValueError
  - guard_application_fields: 'gender' key raises ValueError
  - guard_application_fields: 'disability' key raises ValueError
  - guard_application_fields: case-insensitive check
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.leasing.fair_housing import lint_listing, guard_application_fields


class TestLintListing:
    def test_clean_text_passes(self):
        text = ("Beautiful 3-bedroom home available. "
                "Walking distance to downtown. Pet-friendly.")
        result = lint_listing(text)
        assert result.ok is True
        assert result.flags == []

    def test_no_children_fails(self):
        result = lint_listing("Quiet apartment, no children preferred.")
        assert result.ok is False
        assert any("familial" in f for f in result.flags)

    def test_adults_only_fails(self):
        result = lint_listing("Adults only building, 18+.")
        assert result.ok is False

    def test_no_kids_fails(self):
        result = lint_listing("No kids allowed in this unit.")
        assert result.ok is False

    def test_no_section_8_fails(self):
        result = lint_listing("No Section 8 accepted.")
        assert result.ok is False
        assert any("source-of-income" in f for f in result.flags)

    def test_no_housing_vouchers_fails(self):
        result = lint_listing("We do not accept housing vouchers.")
        assert result.ok is False

    def test_males_only_fails(self):
        result = lint_listing("Males only, shared house.")
        assert result.ok is False
        assert any("sex" in f for f in result.flags)

    def test_females_only_fails(self):
        result = lint_listing("Females only preferred.")
        assert result.ok is False

    def test_multiple_violations_all_reported(self):
        text = "No children, no section 8, adults only unit."
        result = lint_listing(text)
        assert result.ok is False
        assert len(result.flags) >= 2

    def test_case_insensitive(self):
        result = lint_listing("NO CHILDREN ALLOWED IN THIS UNIT")
        assert result.ok is False

    def test_empty_text_passes(self):
        result = lint_listing("")
        assert result.ok is True


class TestGuardApplicationFields:
    def test_clean_dict_passes(self):
        guard_application_fields({
            "monthly_income": 5000,
            "credit_score": 650,
            "eviction_history": False,
        })

    def test_race_field_raises(self):
        with pytest.raises(ValueError, match="race"):
            guard_application_fields({"monthly_income": 5000, "race": "Hispanic"})

    def test_gender_field_raises(self):
        with pytest.raises(ValueError, match="gender"):
            guard_application_fields({"gender": "female", "monthly_income": 4000})

    def test_disability_field_raises(self):
        with pytest.raises(ValueError, match="disability"):
            guard_application_fields({"disability": True, "monthly_income": 4000})

    def test_national_origin_raises(self):
        with pytest.raises(ValueError):
            guard_application_fields({"national_origin": "Mexico"})

    def test_religion_field_raises(self):
        with pytest.raises(ValueError):
            guard_application_fields({"religion": "Catholic"})

    def test_case_insensitive_check(self):
        with pytest.raises(ValueError):
            guard_application_fields({"RACE": "White"})

    def test_empty_dict_passes(self):
        guard_application_fields({})
