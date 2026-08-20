"""
Claude vision condition reader for the ARV/MAO agent.
Sends a Street View exterior photo to Claude and extracts a structured
condition assessment.

Returns a dict with:
  condition     : str   one of good/fair/medium/poor/distressed
  confidence    : str   high / medium / low
  visible_flags : list  observable defects driving the rating
"""

from __future__ import annotations

import base64
import json
import logging
import os

import anthropic

log = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
_MODEL  = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")

_SYSTEM_PROMPT = """\
You are a property condition inspector reviewing a Street View exterior photo \
of a residential property in Indianapolis, Indiana.

Rate the property's exterior condition using exactly one of these tiers:
  good       — well maintained, no visible issues, move-in ready exterior
  fair       — minor wear, cosmetic issues only (paint, landscaping)
  medium     — moderate deferred maintenance (aging roof, dated siding, worn trim)
  poor       — significant issues (multiple systems visibly failing)
  distressed — severe deterioration, possible structural/safety concerns

Return ONLY a JSON object with these exact keys:
{
  "condition":     "<one of the five tiers above>",
  "confidence":    "<high|medium|low>",
  "visible_flags": ["<specific observation>", ...]
}

visible_flags examples: "peeling_paint", "damaged_roof", "broken_windows",
"overgrown_vegetation", "boarded_windows", "foundation_cracks",
"missing_gutters", "deteriorated_siding", "fire_damage", "structural_lean".
List only what is clearly visible. Empty array if nothing notable.

Use low confidence when the image angle, lighting, or resolution makes a
reliable assessment difficult. No preamble, no markdown, no extra text."""

_VALID_CONDITIONS  = {"good", "fair", "medium", "poor", "distressed"}
_VALID_CONFIDENCES = {"high", "medium", "low"}


def read_condition(image_bytes: bytes) -> dict:
    """
    Send image_bytes (JPEG/PNG) to Claude vision and return a condition dict.

    Raises ValueError on bad/missing JSON or invalid field values so the
    caller can catch and fall back gracefully.
    """
    b64 = base64.standard_b64encode(image_bytes).decode()

    response = _client.messages.create(
        model=_MODEL,
        max_tokens=512,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type":       "image",
                        "source":     {
                            "type":       "base64",
                            "media_type": "image/jpeg",
                            "data":       b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Assess this property's exterior condition.",
                    },
                ],
            }
        ],
    )

    raw = response.content[0].text.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude vision returned non-JSON: {raw!r}") from exc

    condition  = result.get("condition", "").lower()
    confidence = result.get("confidence", "").lower()
    flags      = result.get("visible_flags", [])

    if condition not in _VALID_CONDITIONS:
        raise ValueError(f"Unexpected condition value: {condition!r}")
    if confidence not in _VALID_CONFIDENCES:
        raise ValueError(f"Unexpected confidence value: {confidence!r}")
    if not isinstance(flags, list):
        flags = []

    return {
        "condition":     condition,
        "confidence":    confidence,
        "visible_flags": [str(f) for f in flags],
    }
