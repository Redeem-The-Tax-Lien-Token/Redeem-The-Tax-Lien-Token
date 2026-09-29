"""
Decision memo writer for Agent 8 (§3 Step 5).

Claude writes a plain-English explanation of the strategy decision from
the computed numbers only. Claude cannot change the decision, the numbers,
or the offer price — it only explains them.

Uses CLAUDE_MODEL_REASONING (claude-opus-5) as required by §5.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_MODEL = os.environ.get("CLAUDE_MODEL_REASONING", "claude-opus-5")
_ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_SYSTEM_MODE = os.environ.get("SYSTEM_MODE", "live")

_SYSTEM_PROMPT = """\
You are an underwriting analyst explaining a real estate strategy decision to an operator.
The decision and all numbers have already been computed by deterministic Python code.
Your job is to explain them in plain English — three paragraphs:

1. Why the chosen strategy won (reference the specific numbers).
2. The three biggest risks.
3. What specific change would flip the decision to the other strategy (or to NURTURE).

Rules:
- Never change, reframe, or challenge the numbers or the decision.
- Never add numbers not present in the input.
- Maximum 250 words total.
- Write directly to the operator. Be concise.
"""


def write_decision_memo(
    *,
    strategy: str,
    wholesale_case: dict | None,
    brrrr_case: dict | None,
    offer_amount: float,
    fallback_fee: float | None,
    fallback_flag: str | None,
) -> str:
    """
    Call Claude to write the decision memo.

    In dry-run mode, returns a placeholder string without calling the API.
    Raises RuntimeError on API failure (fail closed — no Gate A packet without a memo).
    """
    if _SYSTEM_MODE == "dry_run":
        return _dry_run_memo(strategy, wholesale_case, brrrr_case, offer_amount)

    if not _ANTHROPIC_KEY:
        log.warning("ANTHROPIC_API_KEY not set — returning stub memo")
        return _dry_run_memo(strategy, wholesale_case, brrrr_case, offer_amount)

    try:
        import anthropic  # type: ignore[import-untyped]
    except ImportError:
        log.warning("anthropic package not installed — returning stub memo")
        return _dry_run_memo(strategy, wholesale_case, brrrr_case, offer_amount)

    input_summary = {
        "chosen_strategy": strategy,
        "offer_amount": offer_amount,
        "fallback_fee": fallback_fee,
        "fallback_flag": fallback_flag,
        "wholesale_case": wholesale_case,
        "brrrr_case": brrrr_case,
    }

    user_message = (
        f"Here are the computed underwriting numbers for a deal in Indianapolis:\n\n"
        f"```json\n{json.dumps(input_summary, indent=2, default=str)}\n```\n\n"
        f"Write the three-paragraph decision memo as instructed."
    )

    try:
        client = anthropic.Anthropic(api_key=_ANTHROPIC_KEY)
        response = client.messages.create(
            model=_MODEL,
            max_tokens=400,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        log.error("Claude decision memo failed: %s", exc)
        raise RuntimeError(f"Decision memo API call failed: {exc}") from exc


def _dry_run_memo(
    strategy: str,
    wholesale_case: dict | None,
    brrrr_case: dict | None,
    offer_amount: float,
) -> str:
    wc = wholesale_case or {}
    bc = brrrr_case or {}
    fee    = wc.get("assignment_fee", 0)
    dscr   = bc.get("dscr", 0)
    coc    = bc.get("cash_on_cash", 0)
    cli    = bc.get("cash_left_in", 0)
    return (
        f"[DRY-RUN MEMO] Strategy: {strategy.upper()} at ${offer_amount:,.0f}. "
        f"Wholesale fee: ${fee:,.0f}. "
        f"BRRRR DSCR: {dscr:.2f}, CoC: {coc:.1%}, cash left in: ${cli:,.0f}. "
        f"Full memo suppressed in dry-run mode."
    )
