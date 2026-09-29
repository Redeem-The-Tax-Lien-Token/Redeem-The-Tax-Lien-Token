"""
Unit tests for shared.compliance.disclosures.

Tests: load_solicitation_disclosure, build_seller_sms.
These run without Twilio or database — pure Python.
"""

import importlib
import sys
import textwrap
import types
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────

_TEMPLATE_PATH = (
    Path(__file__).parent.parent
    / "shared" / "compliance" / "templates" / "hb1068_solicitation.txt"
)

_GOOD_TEMPLATE = textwrap.dedent("""\
    # version: 1.0-test
    # Test template — not the real disclosure.
    ---
    TEST DISCLOSURE: Buyer is an investor. Price may be below market value.
""")

_DISCLOSURE_TEXT = "TEST DISCLOSURE: Buyer is an investor. Price may be below market value."


def _reload_disclosures():
    """Reload disclosures module so lru_cache picks up any patched template."""
    mod_name = "shared.compliance.disclosures"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    import shared.compliance.disclosures as m
    return m


# ── Tests: load_solicitation_disclosure ──────────────────────────────────────

def test_load_disclosure_reads_version_and_text(tmp_path, monkeypatch):
    tpl = tmp_path / "hb1068_solicitation.txt"
    tpl.write_text(_GOOD_TEMPLATE)
    import shared.compliance.disclosures as m
    monkeypatch.setattr(m, "_TEMPLATE_PATH", tpl)
    m.load_solicitation_disclosure.cache_clear()
    text, version = m.load_solicitation_disclosure()
    assert version == "1.0-test"
    assert text == _DISCLOSURE_TEXT


def test_load_disclosure_missing_file_raises(tmp_path, monkeypatch):
    import shared.compliance.disclosures as m
    monkeypatch.setattr(m, "_TEMPLATE_PATH", tmp_path / "nonexistent.txt")
    m.load_solicitation_disclosure.cache_clear()
    with pytest.raises(RuntimeError, match="not found"):
        m.load_solicitation_disclosure()


def test_load_disclosure_missing_separator_raises(tmp_path, monkeypatch):
    bad = tmp_path / "bad.txt"
    bad.write_text("# version: 1.0\nSome text without dashes")
    import shared.compliance.disclosures as m
    monkeypatch.setattr(m, "_TEMPLATE_PATH", bad)
    m.load_solicitation_disclosure.cache_clear()
    with pytest.raises(RuntimeError, match="separator"):
        m.load_solicitation_disclosure()


def test_load_disclosure_missing_version_raises(tmp_path, monkeypatch):
    bad = tmp_path / "bad.txt"
    bad.write_text("No version line here\n---\nSome disclosure")
    import shared.compliance.disclosures as m
    monkeypatch.setattr(m, "_TEMPLATE_PATH", bad)
    m.load_solicitation_disclosure.cache_clear()
    with pytest.raises(RuntimeError, match="version"):
        m.load_solicitation_disclosure()


# ── Tests: build_seller_sms ───────────────────────────────────────────────────

def test_build_seller_sms_structure(tmp_path, monkeypatch):
    tpl = tmp_path / "hb1068_solicitation.txt"
    tpl.write_text(_GOOD_TEMPLATE)
    import shared.compliance.disclosures as m
    monkeypatch.setattr(m, "_TEMPLATE_PATH", tpl)
    m.load_solicitation_disclosure.cache_clear()
    body = "Hi there, would you consider a cash offer?"
    msg, version = m.build_seller_sms(body)
    assert msg.startswith(_DISCLOSURE_TEXT), "Disclosure must be first"
    assert m.TCPA_OPT_OUT_FOOTER in msg, "Opt-out footer must be present"
    assert body in msg, "Original body must be preserved"
    assert version == "1.0-test"


def test_build_seller_sms_does_not_double_add_opt_out(tmp_path, monkeypatch):
    tpl = tmp_path / "hb1068_solicitation.txt"
    tpl.write_text(_GOOD_TEMPLATE)
    import shared.compliance.disclosures as m
    monkeypatch.setattr(m, "_TEMPLATE_PATH", tpl)
    m.load_solicitation_disclosure.cache_clear()
    body = f"Offer? {m.TCPA_OPT_OUT_FOOTER}"
    msg, _ = m.build_seller_sms(body)
    assert msg.count(m.TCPA_OPT_OUT_FOOTER) == 1
