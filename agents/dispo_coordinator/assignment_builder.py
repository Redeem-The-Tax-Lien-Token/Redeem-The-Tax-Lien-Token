"""
Assignment agreement builder for Agent 11.

Renders the Assignment of Purchase and Sale Agreement from the Jinja2
template, injecting the buyer-facing HB 1068 assignment disclosure by
code — never from the LLM (§2.1).

Public API:
    rendered, version = build_assignment(...)
"""

from __future__ import annotations

import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from zoneinfo import ZoneInfo

_TEMPLATE_DIR   = Path(__file__).parent.parent.parent / "shared" / "compliance" / "templates"
_ASSIGN_TEMPLATE = "assignment_agreement.j2"
_ASSIGN_DISC_FILE = "assignment_disclosure.txt"

_IND_TZ = ZoneInfo("America/Indiana/Indianapolis")

# Shared with contract_builder — duplicated to keep modules independent
_ONES = [
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _dollars_to_words(n: int) -> str:
    if n == 0:
        return "zero dollars"
    parts: list[str] = []
    if n >= 1000:
        h = n // 1000
        parts.append(f"{_dollars_to_words(h).replace(' dollars', '')} thousand")
        n %= 1000
    if n >= 100:
        parts.append(f"{_ONES[n // 100]} hundred")
        n %= 100
    if n >= 20:
        word = _TENS[n // 10]
        if n % 10:
            word += f"-{_ONES[n % 10]}"
        parts.append(word)
    elif n > 0:
        parts.append(_ONES[n])
    return " ".join(parts) + " dollars"


@lru_cache(maxsize=1)
def _load_assignment_disclosure() -> tuple[str, str]:
    """
    Load the assignment disclosure from assignment_disclosure.txt.
    Returns (disclosure_text, version_string).
    Raises RuntimeError if missing or malformed (fail closed).
    """
    path = _TEMPLATE_DIR / _ASSIGN_DISC_FILE
    if not path.exists():
        raise RuntimeError(
            f"Assignment disclosure template not found: {path}. "
            "Cannot render an assignment agreement without it (fail closed)."
        )
    raw = path.read_text(encoding="utf-8")
    match = re.match(r"^version:\s*(.+?)\s*\n---\n(.+)$", raw, re.DOTALL)
    if not match:
        raise RuntimeError(
            f"Assignment disclosure template malformed: {path}"
        )
    version = match.group(1).strip()
    text = match.group(2).strip()
    if not text:
        raise RuntimeError(f"Assignment disclosure template has empty text: {path}")
    return text, version


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )


def build_assignment(
    *,
    assignor_name: str,
    assignee_name: str,
    seller_name: str,
    property_address: str,
    property_city: str,
    property_state: str,
    property_zip: str,
    psa_price: int,
    psa_date: date,
    assignment_fee: int,
    assignment_emd: int,
    closing_date: date,
    execution_date: date | None = None,
    cfg: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    Render the Assignment of PSA.

    Parameters
    ----------
    assignor_name  : entity name (e.g. "Redeem Real Estate LLC and/or assigns")
    assignee_name  : buyer's full legal name / entity
    seller_name    : original seller under the PSA
    psa_price      : PSA purchase price (whole dollars)
    psa_date       : date the PSA was executed
    assignment_fee : total assignment fee (whole dollars)
    assignment_emd : non-refundable EMD due on assignment (whole dollars)
    closing_date   : PSA closing deadline (date object)
    execution_date : today by default

    Returns
    -------
    (rendered_text, disclosure_version)
    """
    assignment_disclosure, disclosure_version = _load_assignment_disclosure()

    if execution_date is None:
        execution_date = datetime.now(tz=_IND_TZ).date()

    if cfg and not assignor_name:
        assignor_name = cfg.get("contract", {}).get("entity_name", "Redeem Real Estate LLC and/or assigns")

    balance = assignment_fee - assignment_emd

    context = {
        "assignment_disclosure":       assignment_disclosure,
        "disclosure_version":          disclosure_version,
        "execution_date":              execution_date.strftime("%B %d, %Y"),
        "assignor_name":               assignor_name,
        "assignee_name":               assignee_name,
        "seller_name":                 seller_name,
        "property_address":            property_address,
        "property_city":               property_city,
        "property_state":              property_state,
        "property_zip":                property_zip,
        "psa_date":                    psa_date.strftime("%B %d, %Y"),
        "psa_price_formatted":         f"${psa_price:,.0f}",
        "assignment_fee_formatted":    f"${assignment_fee:,.0f}",
        "assignment_fee_words":        _dollars_to_words(assignment_fee),
        "assignment_emd_formatted":    f"${assignment_emd:,.0f}",
        "assignment_fee_balance_formatted": f"${balance:,.0f}",
        "closing_date":                closing_date.strftime("%B %d, %Y"),
    }

    env = _jinja_env()
    template = env.get_template(_ASSIGN_TEMPLATE)
    rendered = template.render(**context)
    return rendered, disclosure_version


def invalidate_cache() -> None:
    _load_assignment_disclosure.cache_clear()
