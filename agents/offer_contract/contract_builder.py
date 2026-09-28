"""
Contract builder for Agent 8 (Offer & Contract).

Renders the Purchase and Sale Agreement from the Jinja2 template, injecting
the HB 1068 PSA disclosure by code — never from the LLM.

Public API:
    rendered, version = build_psa(lead, deal_params, cfg)
    # rendered: str (the rendered contract text)
    # version: str (disclosure version stored on the deal for audit)
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from zoneinfo import ZoneInfo

_TEMPLATE_DIR = Path(__file__).parent.parent.parent / "shared" / "compliance" / "templates"
_PSA_TEMPLATE  = "purchase_agreement.j2"
_PSA_DISC_FILE = "psa_disclosure.txt"

_IND_TZ = ZoneInfo("America/Indiana/Indianapolis")

_ONES = [
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _dollars_to_words(n: int) -> str:
    """Convert an integer dollar amount to English words (up to 999,999)."""
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
def _load_psa_disclosure() -> tuple[str, str]:
    """
    Load the PSA-level HB 1068 disclosure from psa_disclosure.txt.
    Returns (disclosure_text, version_string).
    Raises RuntimeError if missing or malformed (fail closed — no contracts without it).
    """
    path = _TEMPLATE_DIR / _PSA_DISC_FILE
    if not path.exists():
        raise RuntimeError(
            f"PSA disclosure template not found: {path}. "
            "Cannot render a purchase agreement without it (fail closed)."
        )
    raw = path.read_text(encoding="utf-8")
    match = re.match(r"^version:\s*(.+?)\s*\n---\n(.+)$", raw, re.DOTALL)
    if not match:
        raise RuntimeError(
            f"PSA disclosure template malformed (expected 'version: ...\\n---\\n...'): {path}"
        )
    version = match.group(1).strip()
    text = match.group(2).strip()
    if not text:
        raise RuntimeError(f"PSA disclosure template has empty disclosure text: {path}")
    return text, version


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )


def build_psa(
    *,
    seller_name: str,
    property_address: str,
    property_city: str,
    property_state: str,
    property_zip: str,
    attom_id: str | None,
    purchase_price: int,
    emd_amount: int,
    inspection_period_days: int,
    closing_date: date,
    execution_date: date | None = None,
    cfg: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    Render the Purchase and Sale Agreement for a single offer.

    Parameters
    ----------
    seller_name             : str   — seller's full name from the lead record
    property_address        : str   — street address
    property_city/state/zip : str
    attom_id                : str | None
    purchase_price          : int   — offer price in whole dollars
    emd_amount              : int   — earnest money deposit in whole dollars
    inspection_period_days  : int   — calendar days for the inspection period
    closing_date            : date  — closing deadline (must not be in unavailable window)
    execution_date          : date  — today by default
    cfg                     : dict  — parsed strategy.yaml; reads contract.entity_name

    Returns
    -------
    (rendered_text, disclosure_version)

    The caller must store disclosure_version on deals.underwriting_snapshot
    (or deals.notes) for audit trail.
    """
    psa_disclosure, disclosure_version = _load_psa_disclosure()

    if execution_date is None:
        execution_date = datetime.now(tz=_IND_TZ).date()

    entity_name = "Redeem Real Estate LLC and/or assigns"
    if cfg:
        entity_name = cfg.get("contract", {}).get("entity_name", entity_name)

    context = {
        "psa_disclosure":          psa_disclosure,
        "disclosure_version":      disclosure_version,
        "execution_date":          execution_date.strftime("%B %d, %Y"),
        "buyer_name":              entity_name,
        "seller_name":             seller_name,
        "property_address":        property_address,
        "property_city":           property_city,
        "property_state":          property_state,
        "property_zip":            property_zip,
        "attom_id":                attom_id or "TBD",
        "purchase_price_formatted": f"${purchase_price:,.0f}",
        "purchase_price_words":    _dollars_to_words(purchase_price),
        "emd_formatted":           f"${emd_amount:,.0f}",
        "inspection_period_days":  inspection_period_days,
        "closing_date":            closing_date.strftime("%B %d, %Y"),
    }

    env = _jinja_env()
    template = env.get_template(_PSA_TEMPLATE)
    rendered = template.render(**context)
    return rendered, disclosure_version


def invalidate_cache() -> None:
    """Clear the LRU-cached disclosure (for testing)."""
    _load_psa_disclosure.cache_clear()
