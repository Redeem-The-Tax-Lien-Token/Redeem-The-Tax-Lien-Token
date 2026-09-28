"""
Unit tests for agents/offer-contract/contract_builder.py.

All tests are pure Python — no database access, no LLM calls, no network.

Coverage goals:
  - PSA disclosure always present in every rendered contract
  - Buyer name always contains "and/or assigns"
  - Closing date appears correctly in the rendered text
  - Seller name, address, price, EMD, inspection period rendered correctly
  - _dollars_to_words edge cases
  - PSA disclosure template format validation (version + separator)
  - Seller name is never truncated by the disclosure block
  - build_psa reads entity_name from cfg
  - Missing/malformed disclosure template raises RuntimeError (fail closed)
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.offer_contract.contract_builder import (
    build_psa,
    _dollars_to_words,
    _load_psa_disclosure,
    invalidate_cache,
)

_DISC_PATH = Path(__file__).parent.parent / "shared" / "compliance" / "templates" / "psa_disclosure.txt"


# ── helpers ───────────────────────────────────────────────────────────────────

def _psa(**kwargs) -> tuple[str, str]:
    """Render a contract with sensible defaults; override any field via kwargs."""
    defaults = dict(
        seller_name="John Doe",
        property_address="1234 Elm Street",
        property_city="Indianapolis",
        property_state="IN",
        property_zip="46201",
        attom_id="ATM123",
        purchase_price=55_000,
        emd_amount=1_000,
        inspection_period_days=10,
        closing_date=date(2026, 11, 14),
    )
    defaults.update(kwargs)
    invalidate_cache()
    return build_psa(**defaults)


# ── disclosure always present ─────────────────────────────────────────────────

class TestDisclosurePresence:
    def test_disclosure_text_in_rendered_contract(self):
        rendered, _ = _psa()
        # The disclosure text from psa_disclosure.txt must appear in the contract
        disc_text, _ = _load_psa_disclosure()
        assert disc_text in rendered

    def test_disclosure_not_empty(self):
        disc_text, version = _load_psa_disclosure()
        assert disc_text.strip()
        assert version.strip()

    def test_disclosure_version_returned(self):
        _, version = _psa()
        assert version  # non-empty string
        assert "pending" in version or "." in version  # e.g. "0.1-pending-attorney"

    def test_ic_reference_in_disclosure(self):
        # The statutory citation must appear in the PSA disclosure
        disc_text, _ = _load_psa_disclosure()
        assert "IC 32-21-16.5" in disc_text or "32-21" in disc_text

    def test_disclosure_precedes_signature_block(self):
        rendered, _ = _psa()
        disc_text, _ = _load_psa_disclosure()
        disc_pos = rendered.index(disc_text)
        sig_pos = rendered.index("SELLER SIGNATURE")
        assert disc_pos < sig_pos


# ── buyer name ────────────────────────────────────────────────────────────────

class TestBuyerName:
    def test_buyer_contains_and_or_assigns(self):
        rendered, _ = _psa()
        assert "and/or assigns" in rendered

    def test_buyer_name_from_cfg_entity_name(self):
        cfg = {"contract": {"entity_name": "Proffitt Investments LLC and/or assigns"}}
        rendered, _ = _psa(cfg=cfg)
        assert "Proffitt Investments LLC and/or assigns" in rendered

    def test_default_entity_name_when_no_cfg(self):
        rendered, _ = _psa(cfg=None)
        assert "Redeem Real Estate LLC and/or assigns" in rendered

    def test_buyer_name_in_parties_section(self):
        rendered, _ = _psa()
        # The buyer name must appear in section 1 (PARTIES)
        parties_idx = rendered.index("1. PARTIES")
        buyer_idx   = rendered.index("and/or assigns")
        assert buyer_idx > parties_idx


# ── closing date ──────────────────────────────────────────────────────────────

class TestClosingDate:
    def test_closing_date_in_rendered_contract(self):
        closing = date(2026, 12, 1)
        rendered, _ = _psa(closing_date=closing)
        assert "December 01, 2026" in rendered

    def test_closing_date_in_section_6(self):
        closing = date(2026, 12, 15)
        rendered, _ = _psa(closing_date=closing)
        sec6_idx     = rendered.index("6. CLOSING")
        date_idx     = rendered.index("December 15, 2026")
        assert date_idx > sec6_idx

    def test_different_closing_dates_produce_different_text(self):
        r1, _ = _psa(closing_date=date(2026, 11, 1))
        r2, _ = _psa(closing_date=date(2026, 12, 1))
        assert r1 != r2


# ── other required fields ─────────────────────────────────────────────────────

class TestRequiredFields:
    def test_seller_name_present(self):
        rendered, _ = _psa(seller_name="Jane Smith")
        assert "Jane Smith" in rendered

    def test_property_address_present(self):
        rendered, _ = _psa(property_address="999 Oak Ave")
        assert "999 Oak Ave" in rendered

    def test_purchase_price_formatted(self):
        rendered, _ = _psa(purchase_price=65_000)
        assert "$65,000" in rendered

    def test_purchase_price_in_words(self):
        rendered, _ = _psa(purchase_price=65_000)
        assert "sixty-five thousand dollars" in rendered

    def test_emd_formatted(self):
        rendered, _ = _psa(emd_amount=2_500)
        assert "$2,500" in rendered

    def test_inspection_period_days_present(self):
        rendered, _ = _psa(inspection_period_days=14)
        assert "14 calendar days" in rendered

    def test_assignability_clause_present(self):
        rendered, _ = _psa()
        assert "ASSIGNABILITY" in rendered or "BUYER MAY ASSIGN" in rendered

    def test_as_is_clause_present(self):
        rendered, _ = _psa()
        assert "AS-IS" in rendered


# ── _dollars_to_words ─────────────────────────────────────────────────────────

class TestDollarsToWords:
    def test_zero(self):
        assert _dollars_to_words(0) == "zero dollars"

    def test_small(self):
        assert _dollars_to_words(5) == "five dollars"

    def test_teens(self):
        assert _dollars_to_words(15) == "fifteen dollars"

    def test_tens(self):
        assert _dollars_to_words(30) == "thirty dollars"

    def test_compound_tens(self):
        assert _dollars_to_words(45) == "forty-five dollars"

    def test_hundreds(self):
        assert _dollars_to_words(100) == "one hundred dollars"

    def test_hundreds_compound(self):
        assert _dollars_to_words(123) == "one hundred twenty-three dollars"

    def test_thousands(self):
        assert _dollars_to_words(1_000) == "one thousand dollars"

    def test_fifty_five_thousand(self):
        assert _dollars_to_words(55_000) == "fifty-five thousand dollars"

    def test_sixty_five_thousand(self):
        assert _dollars_to_words(65_000) == "sixty-five thousand dollars"

    def test_one_hundred_thousand(self):
        assert _dollars_to_words(100_000) == "one hundred thousand dollars"

    def test_mixed(self):
        assert _dollars_to_words(84_500) == "eighty-four thousand five hundred dollars"


# ── fail-closed: bad disclosure template ─────────────────────────────────────

class TestFailClosed:
    def test_missing_disclosure_file_raises(self, tmp_path, monkeypatch):
        """build_psa must raise RuntimeError if the PSA disclosure file is missing."""
        import agents.offer_contract.contract_builder as cb
        monkeypatch.setattr(cb, "_TEMPLATE_DIR", tmp_path)
        cb.invalidate_cache()
        with pytest.raises(RuntimeError, match="not found"):
            _psa()

    def test_malformed_disclosure_raises(self, tmp_path, monkeypatch):
        """RuntimeError if the disclosure file lacks the 'version:' header."""
        import agents.offer_contract.contract_builder as cb
        disc_file = tmp_path / "psa_disclosure.txt"
        disc_file.write_text("no version here\n---\nsome disclosure\n")
        monkeypatch.setattr(cb, "_TEMPLATE_DIR", tmp_path)
        cb.invalidate_cache()
        with pytest.raises(RuntimeError):
            _psa()

    def test_empty_disclosure_raises(self, tmp_path, monkeypatch):
        """RuntimeError if the disclosure text after '---' is blank."""
        import agents.offer_contract.contract_builder as cb
        disc_file = tmp_path / "psa_disclosure.txt"
        disc_file.write_text("version: 0.1-test\n---\n\n")
        monkeypatch.setattr(cb, "_TEMPLATE_DIR", tmp_path)
        cb.invalidate_cache()
        with pytest.raises(RuntimeError, match="empty"):
            _psa()

    def test_contract_template_renders_disclosure_verbatim(self):
        """The disclosure text must appear verbatim (not paraphrased) in the PSA."""
        rendered, _ = _psa()
        disc_text, _ = _load_psa_disclosure()
        # Every word in the disclosure must appear in the contract
        for word in disc_text.split():
            assert word in rendered, f"Disclosure word '{word}' missing from PSA"
