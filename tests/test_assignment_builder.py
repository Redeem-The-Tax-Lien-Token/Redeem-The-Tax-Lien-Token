"""
Unit tests for agents/dispo_coordinator/assignment_builder.py.

All tests are pure Python — no database, no network.

Coverage:
  - Assignment disclosure always present and verbatim in rendered contract
  - IC 32-21-16.5 citation present
  - Assignor name always present (and never overrides "and/or assigns" structure)
  - Assignee name present
  - Assignment fee, EMD, balance rendered correctly
  - PSA price and closing date appear
  - Assignability language present
  - Fail-closed on missing/malformed/empty disclosure
  - _dollars_to_words edge cases
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.dispo_coordinator.assignment_builder import (
    build_assignment,
    _dollars_to_words,
    _load_assignment_disclosure,
    invalidate_cache,
)

_DISC_PATH = Path(__file__).parent.parent / "shared" / "compliance" / "templates" / "assignment_disclosure.txt"


# ── helpers ───────────────────────────────────────────────────────────────────

def _assign(**kwargs) -> tuple[str, str]:
    defaults = dict(
        assignor_name="Redeem Real Estate LLC and/or assigns",
        assignee_name="John Cash Buyer LLC",
        seller_name="Jane Seller",
        property_address="5678 Oak Street",
        property_city="Indianapolis",
        property_state="IN",
        property_zip="46203",
        psa_price=55_000,
        psa_date=date(2026, 10, 1),
        assignment_fee=10_000,
        assignment_emd=1_000,
        closing_date=date(2026, 11, 14),
    )
    defaults.update(kwargs)
    invalidate_cache()
    return build_assignment(**defaults)


# ── disclosure always present ─────────────────────────────────────────────────

class TestDisclosurePresence:
    def test_disclosure_text_in_rendered_assignment(self):
        rendered, _ = _assign()
        disc_text, _ = _load_assignment_disclosure()
        assert disc_text in rendered

    def test_disclosure_not_empty(self):
        disc_text, version = _load_assignment_disclosure()
        assert disc_text.strip()
        assert version.strip()

    def test_disclosure_version_returned(self):
        _, version = _assign()
        assert version
        assert "pending" in version or "." in version

    def test_ic_reference_in_disclosure(self):
        disc_text, _ = _load_assignment_disclosure()
        assert "IC 32-21-16.5" in disc_text or "32-21" in disc_text

    def test_disclosure_precedes_signature_block(self):
        rendered, _ = _assign()
        disc_text, _ = _load_assignment_disclosure()
        disc_pos  = rendered.index(disc_text)
        sig_pos   = rendered.index("ASSIGNOR SIGNATURE")
        assert disc_pos < sig_pos

    def test_disclosure_verbatim(self):
        rendered, _ = _assign()
        disc_text, _ = _load_assignment_disclosure()
        for word in disc_text.split():
            assert word in rendered


# ── party names ───────────────────────────────────────────────────────────────

class TestPartyNames:
    def test_assignor_name_present(self):
        rendered, _ = _assign(assignor_name="Proffitt Investments LLC and/or assigns")
        assert "Proffitt Investments LLC and/or assigns" in rendered

    def test_assignee_name_present(self):
        rendered, _ = _assign(assignee_name="Big Buyer Corp")
        assert "Big Buyer Corp" in rendered

    def test_seller_name_present(self):
        rendered, _ = _assign(seller_name="Bob Testowner")
        assert "Bob Testowner" in rendered

    def test_assignor_in_parties_section(self):
        rendered, _ = _assign()
        parties_idx  = rendered.index("1. PARTIES")
        assignor_idx = rendered.index("Redeem Real Estate LLC")
        assert assignor_idx > parties_idx


# ── financial fields ──────────────────────────────────────────────────────────

class TestFinancials:
    def test_assignment_fee_formatted(self):
        rendered, _ = _assign(assignment_fee=10_000)
        assert "$10,000" in rendered

    def test_assignment_fee_in_words(self):
        rendered, _ = _assign(assignment_fee=10_000)
        assert "ten thousand dollars" in rendered

    def test_emd_formatted(self):
        rendered, _ = _assign(assignment_emd=1_500)
        assert "$1,500" in rendered

    def test_balance_is_fee_minus_emd(self):
        rendered, _ = _assign(assignment_fee=12_000, assignment_emd=2_000)
        # balance = $10,000
        assert "$10,000" in rendered

    def test_psa_price_formatted(self):
        rendered, _ = _assign(psa_price=65_000)
        assert "$65,000" in rendered

    def test_closing_date_present(self):
        rendered, _ = _assign(closing_date=date(2026, 12, 1))
        assert "December 01, 2026" in rendered

    def test_psa_date_present(self):
        rendered, _ = _assign(psa_date=date(2026, 10, 5))
        assert "October 05, 2026" in rendered


# ── required clauses ─────────────────────────────────────────────────────────

class TestRequiredClauses:
    def test_assignment_clause_present(self):
        rendered, _ = _assign()
        assert "assigns, transfers, and conveys" in rendered or "assignment" in rendered.lower()

    def test_entire_agreement_clause(self):
        rendered, _ = _assign()
        assert "ENTIRE AGREEMENT" in rendered

    def test_governing_law_indiana(self):
        rendered, _ = _assign()
        assert "Indiana" in rendered


# ── fail-closed ───────────────────────────────────────────────────────────────

class TestFailClosed:
    def test_missing_disclosure_file_raises(self, tmp_path, monkeypatch):
        import agents.dispo_coordinator.assignment_builder as ab
        monkeypatch.setattr(ab, "_TEMPLATE_DIR", tmp_path)
        ab.invalidate_cache()
        with pytest.raises(RuntimeError, match="not found"):
            _assign()

    def test_malformed_disclosure_raises(self, tmp_path, monkeypatch):
        import agents.dispo_coordinator.assignment_builder as ab
        disc = tmp_path / "assignment_disclosure.txt"
        disc.write_text("no version\n---\nsome text\n")
        # also need the template
        import shutil
        src_tpl = Path(__file__).parent.parent / "shared" / "compliance" / "templates" / "assignment_agreement.j2"
        shutil.copy(src_tpl, tmp_path / "assignment_agreement.j2")
        monkeypatch.setattr(ab, "_TEMPLATE_DIR", tmp_path)
        ab.invalidate_cache()
        with pytest.raises(RuntimeError):
            _assign()

    def test_empty_disclosure_raises(self, tmp_path, monkeypatch):
        import agents.dispo_coordinator.assignment_builder as ab
        disc = tmp_path / "assignment_disclosure.txt"
        disc.write_text("version: 0.1-test\n---\n\n")
        monkeypatch.setattr(ab, "_TEMPLATE_DIR", tmp_path)
        ab.invalidate_cache()
        with pytest.raises(RuntimeError, match="empty"):
            _assign()


# ── _dollars_to_words ─────────────────────────────────────────────────────────

class TestDollarsToWords:
    def test_zero(self):
        assert _dollars_to_words(0) == "zero dollars"

    def test_ten_thousand(self):
        assert _dollars_to_words(10_000) == "ten thousand dollars"

    def test_twelve_thousand_five_hundred(self):
        assert _dollars_to_words(12_500) == "twelve thousand five hundred dollars"

    def test_one_hundred_thousand(self):
        assert _dollars_to_words(100_000) == "one hundred thousand dollars"
