"""
DNC scrub helper for the Outreach Agent.
Outreach re-scrubs every phone before each campaign — even phones the
skip-tracer already cleared — per the requirement in master-context § 5.5.
FAIL-CLOSED: any exception returns an empty set.
"""

import os
import requests

TRACERFY_BASE = "https://app.tracerfy.com/api"
_HEADERS = {
    "Authorization": f"Bearer {os.environ['TRACERFY_API_KEY']}",
    "Content-Type":  "application/json",
}
_TIMEOUT = 30


def scrub_phones(phone_list: list[str]) -> set[str]:
    """
    POST /v1/dnc-scrub against Federal DNC, State DNC, DMA, TCPA Litigator.
    Accepts E.164 or 10-digit strings; returns clean numbers in the same
    format as provided.

    FAIL-CLOSED: any exception → empty set → zero phones cleared → no sends.
    1 credit per phone.
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
        return set()
