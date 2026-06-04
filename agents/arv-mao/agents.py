"""
Claude API integration for the ARV/MAO agent.
Sends normalized comp data to Claude and parses the returned JSON analysis.
"""

import json
import os

import anthropic

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
_MODEL  = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# System prompt verbatim from master-context-v1.4.md § 9.2
_SYSTEM_PROMPT = """\
You are a real estate deal analyst. Given a set of comparable sales from ATTOM, \
calculate the ARV and MAO.

ARV Methodology:
- Weight closest and most recent comps most heavily
- Adjust for sqft differences (use price/sqft of comps)
- Output Low, Mid, High ARV estimates

MAO Formula: (ARV_mid x 0.70) - repair_estimate - 10000

Return ONLY a JSON object:
{"arv_low": 0, "arv_mid": 0, "arv_high": 0, "confidence": "high/medium/low", \
"mao": 0, "comps_used": 0, "notes": "brief explanation"}

No preamble, no markdown, no extra text."""


def analyze_comps(
    subject: dict,
    comps: list[dict],
    repair_estimate: float,
) -> dict:
    """
    Send subject property + comps to Claude; return parsed ARV/MAO dict.
    Raises ValueError if Claude's response is not valid JSON.
    """
    user_message = json.dumps(
        {
            "subject_property": subject,
            "repair_estimate": repair_estimate,
            "comps": comps,
        },
        indent=2,
    )

    response = _client.messages.create(
        model=_MODEL,
        max_tokens=512,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown fences if Claude wraps the JSON despite instructions
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned non-JSON response: {raw!r}") from exc
