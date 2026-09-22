"""
Unit tests for shared.compliance.linter.check_outbound.

The five required cases per the plan:
  1. Pass: correct message
  2. Fail: disclosure missing entirely
  3. Fail: disclosure present but altered
  4. Fail: disclosure split across segment boundary
  5. Segment count reported correctly (1-seg and 2-seg)
"""

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

_GOOD_TEMPLATE = textwrap.dedent("""\
    # version: 1.0-test
    ---
    DISC: Buyer is investor. Price may be below market.
""")

_DISC = "DISC: Buyer is investor. Price may be below market."
_OPT  = "Reply STOP to unsubscribe."
_VER  = "1.0-test"


def _make_message(disc=_DISC, body="Hi there, cash offer?", opt=_OPT):
    return f"{disc} {body} {opt}"


def _patch_template(monkeypatch, tmp_path):
    tpl = tmp_path / "hb1068_solicitation.txt"
    tpl.write_text(_GOOD_TEMPLATE)
    import shared.compliance.disclosures as d
    monkeypatch.setattr(d, "_TEMPLATE_PATH", tpl)
    d.load_solicitation_disclosure.cache_clear()
    import shared.compliance.linter as linter
    return linter


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_pass_correct_message(monkeypatch, tmp_path):
    linter = _patch_template(monkeypatch, tmp_path)
    msg = _make_message()
    result = linter.check_outbound(msg, _VER)
    assert result.ok
    assert result.segment_count == 1


def test_fail_disclosure_missing(monkeypatch, tmp_path):
    linter = _patch_template(monkeypatch, tmp_path)
    msg = f"Hi there, cash offer? {_OPT}"
    result = linter.check_outbound(msg, _VER)
    assert not result.ok
    assert "missing or altered" in result.reason


def test_fail_disclosure_altered(monkeypatch, tmp_path):
    linter = _patch_template(monkeypatch, tmp_path)
    altered_disc = _DISC.replace("investor", "buyer")  # one word changed
    msg = _make_message(disc=altered_disc)
    result = linter.check_outbound(msg, _VER)
    assert not result.ok


def test_fail_disclosure_split_across_segment(monkeypatch, tmp_path):
    linter = _patch_template(monkeypatch, tmp_path)
    # Pad the message so the disclosure starts beyond char 103 (= 153 - 50 chars of disc)
    # making the disclosure end past char 153 (the first segment boundary)
    padding = "X" * 110  # pushes disc start to char 111
    msg = f"{padding} {_DISC} {_OPT}"
    result = linter.check_outbound(msg, _VER)
    assert not result.ok
    assert "crosses segment boundary" in result.reason


def test_fail_empty_disclosure_version(monkeypatch, tmp_path):
    linter = _patch_template(monkeypatch, tmp_path)
    msg = _make_message()
    result = linter.check_outbound(msg, "")
    assert not result.ok
    assert "disclosure_version" in result.reason


def test_segment_count_single(monkeypatch, tmp_path):
    linter = _patch_template(monkeypatch, tmp_path)
    short_msg = f"{_DISC} short body. {_OPT}"
    assert len(short_msg) <= 160
    result = linter.check_outbound(short_msg, _VER)
    assert result.ok
    assert result.segment_count == 1


def test_segment_count_two_segments(monkeypatch, tmp_path):
    linter = _patch_template(monkeypatch, tmp_path)
    # Build a message that is definitely > 160 chars but fits disc in seg 1
    body = "B" * 130
    msg = f"{_DISC} {body} {_OPT}"
    assert len(msg) > 160
    result = linter.check_outbound(msg, _VER)
    assert result.ok
    assert result.segment_count >= 2
