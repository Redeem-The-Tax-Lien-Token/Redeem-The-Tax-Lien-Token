"""
Voice agent opening script builder for Agent 10.

The opening is spoken at the start of EVERY call, inbound or callback,
before the LLM takes over the conversation.  It is code-rendered — never
written, modified, or omitted by the LLM (§2.1, §7 Agent 10).

The script contains (in this order, as required):
  1. Automated-agent disclosure — caller must know they are talking to AI.
  2. HB 1068 / IC 32-21-16.5 solicitation disclosure — required before any
     substantive real-estate statement.
  3. Brief purpose statement ("I'm calling about / you called about...")

Both disclosures are loaded from versioned template files at startup.
Fail-closed: if either template is missing or malformed, the agent cannot
start and inbound calls must be forwarded to the operator.

Public API:
    script, disc_version = build_opening(lead_context)
    # script:       str — the verbatim text the TTS engine will speak
    # disc_version: str — stored on the outreach_log row for audit
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from shared.compliance.disclosures import load_solicitation_disclosure

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "shared" / "compliance" / "templates"

# Fixed automated-agent disclosure — never altered by LLM
_AUTOMATED_AGENT_DISCLOSURE = (
    "This is an automated message from a real estate investor. "
    "You are speaking with an AI assistant, not a person. "
    "If you would like to speak with a person, please say 'transfer' at any time."
)

# Maximum length of the opening in characters (TTS segment budget)
_MAX_OPENING_CHARS = 600


@lru_cache(maxsize=1)
def _get_solicitation_disclosure() -> tuple[str, str]:
    """Load the HB 1068 solicitation disclosure. Fail-closed."""
    return load_solicitation_disclosure()


def build_opening(
    lead_context: dict | None = None,
    call_type: str = "inbound",
) -> tuple[str, str]:
    """
    Build the verbatim opening script for a voice call.

    Parameters
    ----------
    lead_context : dict | None — lead data (address, owner_name) for context.
                   May be None for unidentified inbound calls.
    call_type    : "inbound" | "callback" — slightly adjusts the purpose statement.

    Returns
    -------
    (opening_text, disclosure_version)

    The opening_text is what the TTS engine speaks verbatim as the
    ConversationRelay welcomeGreeting before the LLM takes over.
    """
    solicitation_disclosure, disclosure_version = _get_solicitation_disclosure()

    # Part 1: Automated-agent disclosure (always first)
    part1 = _AUTOMATED_AGENT_DISCLOSURE

    # Part 2: HB 1068 solicitation disclosure (always before substantive content)
    part2 = solicitation_disclosure

    # Part 3: Purpose statement — brief, no substantive content yet
    if call_type == "callback" and lead_context:
        address = lead_context.get("address", "your property")
        part3 = f"I'm following up about a cash offer for {address}."
    elif call_type == "inbound":
        part3 = "Thank you for calling. How can I help you today?"
    else:
        part3 = "Thank you for your time."

    opening = f"{part1} {part2} {part3}"

    if len(opening) > _MAX_OPENING_CHARS:
        # Truncation would violate compliance — fail rather than send incomplete disclosures
        raise RuntimeError(
            f"Opening script exceeds {_MAX_OPENING_CHARS} chars ({len(opening)}). "
            "Shorten the purpose statement; disclosures cannot be truncated."
        )

    return opening, disclosure_version


def build_system_prompt(lead_context: dict | None = None) -> str:
    """
    Build the LLM system prompt for the ConversationRelay agent.

    The LLM is restricted to:
      - Identifying the lead and confirming known facts.
      - Restating the single offer price (if any, from lead_context).
      - Booking a callback or sending a contract link on acceptance.
      - Transferring to the operator on request.

    The LLM must never:
      - Negotiate or suggest a different price.
      - Make any legal, tax, or valuation statements.
      - Claim to be a licensed agent or broker.
      - Speak any disclosure text (disclosures are pre-rendered in the opening).
    """
    address   = (lead_context or {}).get("address", "the property")
    offer     = (lead_context or {}).get("offer_amount")
    offer_str = f"${offer:,.0f}" if offer else "no offer on file"

    return (
        "You are a polite, professional AI assistant for a real estate investor "
        "in Indianapolis. The caller has already heard the required disclosures. "
        "Your job is to:\n"
        "1. Confirm who is calling and which property they are calling about.\n"
        f"2. The property is: {address}. The current offer on file is: {offer_str}. "
        "Do not change or negotiate this offer under any circumstances.\n"
        "3. If the seller accepts the offer, collect their email and tell them "
        "you will send them a purchase agreement link.\n"
        "4. If the seller wants to speak with a person, say 'transferring you now' "
        "and use the transfer function.\n"
        "5. If the seller has questions you cannot answer (legal, tax, appraisal, "
        "or anything outside the offer), say you will have someone follow up.\n"
        "6. Never claim to be a licensed real estate agent or broker.\n"
        "7. Never give legal, tax, or valuation advice.\n"
        "8. Never negotiate a different price or make a counter-offer.\n"
        "9. Keep responses concise — this is a phone call.\n"
        "10. End the call politely if the seller is not interested."
    )


def invalidate_cache() -> None:
    if hasattr(_get_solicitation_disclosure, "cache_clear"):
        _get_solicitation_disclosure.cache_clear()
