"""
Tracerfy API helpers for the Skip Tracer agent.
Covers instant single-address trace and batch DNC scrub.

CRITICAL — DNC scrub is FAIL-CLOSED (§ 5.5 of master-context):
  If the scrub API errors for any reason, all phones in the batch are
  treated as DNC and NONE are returned. A failed scrub must never let
  an unverified phone through to outreach.
"""

import os
import time

import requests

TRACERFY_BASE = "https://app.tracerfy.com/api"

_HEADERS = {
    "Authorization": f"Bearer {os.environ['TRACERFY_API_KEY']}",
    "Content-Type":  "application/json",
}
_TIMEOUT = 30


# ── Instant trace ─────────────────────────────────────────────────────────────

def trace_single(address: str, city: str, state: str) -> dict:
    """
    POST /v1/trace/instant — synchronous lookup.
    Returns raw Tracerfy response dict.
    Raises requests.HTTPError on non-2xx.
    Charges 5 credits on hit, 0 on miss.
    """
    url = f"{TRACERFY_BASE}/v1/trace/instant"
    payload = {
        "address":    address,
        "city":       city,
        "state":      state,
        "find_owner": True,
    }
    resp = requests.post(url, headers=_HEADERS, json=payload, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ── Batch DNC scrub ───────────────────────────────────────────────────────────

def scrub_phones(phone_list: list[str]) -> set[str]:
    """
    POST /v1/dnc-scrub — scrub against Federal DNC, State DNC, DMA,
    and TCPA Litigator databases.

    FAIL-CLOSED: any exception returns an empty set so the caller never
    sends to an unverified number. 1 credit per phone.

    Returns the set of clean (non-DNC) phone numbers.
    """
    if not phone_list:
        return set()
    url = f"{TRACERFY_BASE}/v1/dnc-scrub"
    try:
        resp = requests.post(
            url,
            headers=_HEADERS,
            json={"phones": phone_list},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return {
            r["phone"] for r in results
            if not r.get("is_dnc") and not r.get("litigator_flag")
        }
    except Exception:
        # Fail closed — no scrub result means no phones cleared
        return set()


# ── Result parsers ────────────────────────────────────────────────────────────

def is_deceased(trace: dict) -> bool:
    return bool(trace.get("deceased"))


def has_litigator_flag(trace: dict) -> bool:
    return bool(trace.get("litigator_flag"))


def extract_phones(trace: dict) -> list[str]:
    """
    Return all phone numbers from a trace result, sorted by rank (1 = best).
    Strips non-digit characters, drops numbers shorter than 10 digits.
    """
    phones = []
    for p in sorted(trace.get("phones", []), key=lambda x: x.get("rank", 99)):
        number = "".join(c for c in str(p.get("number", "")) if c.isdigit())
        if len(number) >= 10:
            phones.append(number[-10:])  # normalize to 10 digits
    return phones


def best_phone(phones_ranked: list[str], clean_phones: set[str]) -> str | None:
    """
    Return the first phone from the ranked list that is in clean_phones.
    Prefers rank-1 (most recent) non-DNC mobile number.
    """
    for p in phones_ranked:
        if p in clean_phones:
            return p
    return None


def extract_email(trace: dict) -> str | None:
    """Return the first email address from a trace result, or None."""
    emails = trace.get("emails", [])
    if emails:
        return emails[0].get("address") or None
    return None
