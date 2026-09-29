"""
Unit tests for agents/voice/opening_script.py.

All tests are pure Python — no database, no network.

Coverage:
  - build_opening: automated-agent disclosure always first
  - build_opening: HB 1068 solicitation disclosure always present
  - build_opening: disclosure appears BEFORE any substantive content
  - build_opening: disclosure version returned for audit
  - build_opening: inbound vs callback context adjusts purpose statement
  - build_opening: disclosure not LLM-generated (content from template only)
  - build_opening: raises RuntimeError if opening exceeds char limit
  - build_system_prompt: never negotiates, never claims licensed status
  - build_system_prompt: mentions transfer capability
  - build_opening: fail-closed when solicitation template missing

Compliance checks:
  - "automated" / "AI" must appear before the HB 1068 disclosure index
  - IC 32-21-16.5 disclosure wording must appear verbatim in every opening
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.voice.opening_script import (
    build_opening,
    build_system_prompt,
    _AUTOMATED_AGENT_DISCLOSURE,
    _MAX_OPENING_CHARS,
    invalidate_cache,
)
from shared.compliance.disclosures import load_solicitation_disclosure


# ── helpers ───────────────────────────────────────────────────────────────────

def _opening(**kwargs) -> tuple[str, str]:
    invalidate_cache()
    return build_opening(**kwargs)


# ── automated-agent disclosure always first ───────────────────────────────────

class TestAutomatedAgentDisclosure:
    def test_automated_disclosure_present(self):
        text, _ = _opening()
        assert "automated" in text.lower() or "AI" in text

    def test_automated_disclosure_first(self):
        text, _ = _opening()
        assert text.startswith(_AUTOMATED_AGENT_DISCLOSURE)

    def test_transfer_instruction_in_opening(self):
        text, _ = _opening()
        assert "transfer" in text.lower()

    def test_not_a_person_stated(self):
        text, _ = _opening()
        assert "AI" in text or "automated" in text.lower() or "not a person" in text.lower()


# ── HB 1068 solicitation disclosure presence ─────────────────────────────────

class TestSolicitationDisclosure:
    def test_solicitation_disclosure_present(self):
        text, _ = _opening()
        disc_text, _ = load_solicitation_disclosure()
        assert disc_text in text

    def test_disclosure_version_returned(self):
        _, version = _opening()
        assert version
        assert "." in version or "pending" in version

    def test_ic_reference_in_opening(self):
        text, _ = _opening()
        disc_text, _ = load_solicitation_disclosure()
        assert "IC 32-21-16.5" in disc_text or "32-21" in disc_text

    def test_disclosure_comes_after_automated_disclosure(self):
        text, _ = _opening()
        disc_text, _ = load_solicitation_disclosure()
        auto_end = len(_AUTOMATED_AGENT_DISCLOSURE)
        disc_pos = text.find(disc_text)
        assert disc_pos >= auto_end, (
            "HB 1068 disclosure must appear after the automated-agent disclosure"
        )

    def test_disclosure_before_substantive_purpose_statement(self):
        lead = {"address": "123 Main St", "owner_name": "Bob"}
        text, _ = _opening(lead_context=lead, call_type="callback")
        disc_text, _ = load_solicitation_disclosure()
        disc_pos = text.find(disc_text)
        purpose_pos = text.find("123 Main St")
        assert disc_pos < purpose_pos, (
            "HB 1068 disclosure must precede the substantive purpose statement"
        )


# ── call type variations ──────────────────────────────────────────────────────

class TestCallType:
    def test_inbound_call_type(self):
        text, _ = _opening(call_type="inbound")
        assert isinstance(text, str)
        assert len(text) > 50

    def test_callback_includes_address(self):
        lead = {"address": "456 Elm Ave", "owner_name": "Sue"}
        text, _ = _opening(lead_context=lead, call_type="callback")
        assert "456 Elm Ave" in text

    def test_callback_without_lead_context(self):
        text, _ = _opening(lead_context=None, call_type="callback")
        assert isinstance(text, str)

    def test_different_call_types_produce_different_openings(self):
        lead = {"address": "789 Oak St"}
        t_in, _  = _opening(lead_context=lead, call_type="inbound")
        t_cb, _  = _opening(lead_context=lead, call_type="callback")
        assert t_in != t_cb


# ── length guard ─────────────────────────────────────────────────────────────

class TestLengthGuard:
    def test_opening_under_max_chars(self):
        text, _ = _opening()
        assert len(text) <= _MAX_OPENING_CHARS

    def test_exceeding_max_chars_raises(self):
        """If opening would exceed _MAX_OPENING_CHARS, fail rather than truncate disclosures."""
        very_long_purpose = "X" * (_MAX_OPENING_CHARS + 200)
        with patch("agents.voice.opening_script._AUTOMATED_AGENT_DISCLOSURE", very_long_purpose):
            with pytest.raises(RuntimeError, match="exceeds"):
                build_opening()


# ── fail-closed ───────────────────────────────────────────────────────────────

class TestFailClosed:
    def test_missing_solicitation_template_raises(self, monkeypatch):
        """build_opening must propagate RuntimeError if the solicitation template is missing."""
        def _raise():
            raise RuntimeError("solicitation template not found")
        import agents.voice.opening_script as vo
        monkeypatch.setattr(vo, "_get_solicitation_disclosure", _raise)
        vo.invalidate_cache()
        with pytest.raises(RuntimeError):
            build_opening()


# ── system prompt constraints ─────────────────────────────────────────────────

class TestSystemPrompt:
    def test_transfer_mentioned(self):
        prompt = build_system_prompt()
        assert "transfer" in prompt.lower()

    def test_no_negotiate_instruction(self):
        prompt = build_system_prompt()
        lower = prompt.lower()
        assert "do not" in lower or "never" in lower
        assert "negotiate" in lower or "counter" in lower or "change" in lower

    def test_no_claim_licensed_agent(self):
        prompt = build_system_prompt()
        assert "licensed" in prompt.lower()   # must PROHIBIT it
        assert "never claim" in prompt.lower() or "not" in prompt.lower()

    def test_offer_shown_when_present(self):
        lead = {"address": "123 Main", "offer_amount": 55000}
        prompt = build_system_prompt(lead_context=lead)
        assert "$55,000" in prompt

    def test_no_offer_fallback(self):
        prompt = build_system_prompt(lead_context={"address": "123 Main"})
        assert "no offer on file" in prompt
