"""
DNC scrub helper for the Deal Marketing Agent.
Buyers receive investment solicitations — TCPA still applies.
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
    Scrub buyer phones against Federal DNC, State DNC, DMA, TCPA Litigator.
    FAIL-CLOSED: exception → empty set → no blast goes out.
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
