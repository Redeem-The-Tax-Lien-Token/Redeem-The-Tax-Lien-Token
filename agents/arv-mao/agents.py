"""
Claude API integration for the ARV/MAO agent.
Returns ARV estimates ONLY — MAO and repair cost are computed in Python
by main.py and repair_estimate.py, not by the model.
"""

import json
import os

import anthropic

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
_MODEL  = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")

_SYSTEM_PROMPT = """\
You are a real estate comparables analyst. Given a subject property and a \
set of recent comparable sales from ATTOM, calculate ARV estimates.

ARV Methodology:
- Weight closest and most recent comps most heavily
- Adjust for square footage differences using price/sqft of comps
- Output Low, Mid, and High ARV estimates

Return ONLY a JSON object with these exact keys:
{"arv_low": 0, "arv_mid": 0, "arv_high": 0, "confidence": "high|medium|low", \
"comps_used": 0, "notes": "brief explanation of comp selection and adjustments"}

No preamble, no markdown, no extra text. Do not include MAO, repair cost, \
or any offer price — those are computed separately."""


def analyze_comps(subject: dict, comps: list[dict]) -> dict:
    """
    Send subject property + comps to Claude; return parsed ARV dict.
    Raises ValueError if Claude's response is not valid JSON.
    """
    user_message = json.dumps(
        {"subject_property": subject, "comps": comps},
        indent=2,
    )

    response = _client.messages.create(
        model=_MODEL,
        max_tokens=512,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned non-JSON response: {raw!r}") from exc
