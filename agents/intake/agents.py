"""
Claude classification logic for the Intake Agent.
Uses the system prompt verbatim from master-context-v1.4.md § 9.1.
"""

import json
import os

import anthropic

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
_MODEL  = os.environ.get("CLAUDE_MODEL_FAST", "claude-sonnet-5")

# Verbatim from master-context-v1.4.md § 9.1
_SYSTEM_PROMPT = """\
You are a real estate lead intake classifier for a wholesale investor in \
Indianapolis, Indiana. Your job is to read an inbound SMS reply from a \
property owner and classify it.

Classify the reply as one of:
  HOT    - Seller is interested, wants to talk, asked for an offer, or gave any positive signal
  WARM   - Seller is curious, not sure, asked a question, or is neutral
  COLD   - Seller said no, not interested, or left
  DNC    - Seller said STOP, remove me, do not contact
  OTHER  - Unclear, unrelated, or spam

Return ONLY a JSON object:
{"classification": "HOT", "reason": "one sentence", "suggested_reply": "short reply text"}

No preamble, no markdown."""

_VALID_CLASSES = {"HOT", "WARM", "COLD", "DNC", "OTHER"}


def classify_reply(body: str) -> dict:
    """
    Classify an inbound SMS body with Claude.
    Returns dict with keys: classification, reason, suggested_reply.
    Raises ValueError if Claude returns non-JSON or an unknown classification.
    """
    response = _client.messages.create(
        model=_MODEL,
        max_tokens=256,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": body.strip()}],
    )

    raw = response.content[0].text.strip()

    # Defensive strip of markdown fences
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned non-JSON: {raw!r}") from exc

    cls = result.get("classification", "").upper()
    if cls not in _VALID_CLASSES:
        raise ValueError(f"Unknown classification from Claude: {cls!r}")

    result["classification"] = cls
    return result
