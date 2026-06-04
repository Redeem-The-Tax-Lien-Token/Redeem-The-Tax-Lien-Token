"""
ATTOM API helpers for the List Puller agent.
Pulls distressed / motivated-seller property lists by zip code.

Motivation types supported:
  pre_foreclosure  — /preforeclosure/snapshot
  high_equity      — /property/snapshot filtered by equity %
  tax_delinquent   — /property/snapshot filtered by tax delinquency flag
  vacant           — /property/snapshot filtered by vacancy flag
"""

import os
from datetime import datetime, timedelta
from typing import Optional

import requests

ATTOM_API_KEY = os.environ["ATTOM_API_KEY"]
BASE_URL      = "https://api.gateway.attomdata.com/propertyapi/v1.0.0"

_HEADERS = {
    "apikey": ATTOM_API_KEY,
    "Accept": "application/json",
}
_TIMEOUT  = 30
_PAGE_SIZE = 50   # ATTOM max is 100; keep lower to stay within rate limits


# ── Motivation-type dispatch ──────────────────────────────────────────────────

def pull_leads(
    zip_code:       str,
    motivation:     str,
    property_type:  str   = "SFR",
    min_equity_pct: float = 30.0,
    min_years_owned: int  = 2,
    max_results:    int   = 50,
) -> list[dict]:
    """
    Pull normalized lead dicts from ATTOM for one zip + motivation type.
    Returns at most max_results records.
    """
    if motivation == "pre_foreclosure":
        return _pull_preforeclosure(zip_code, property_type, max_results)
    if motivation == "high_equity":
        return _pull_snapshot(
            zip_code, property_type, max_results,
            extra_params={"mineq": int(min_equity_pct)},
            motivation=motivation,
        )
    if motivation == "tax_delinquent":
        return _pull_snapshot(
            zip_code, property_type, max_results,
            extra_params={"taxdelinquent": "Y"},
            motivation=motivation,
        )
    if motivation == "vacant":
        return _pull_snapshot(
            zip_code, property_type, max_results,
            extra_params={"vacant": "Y"},
            motivation=motivation,
        )
    raise ValueError(f"Unknown motivation type: {motivation!r}")


# ── Pre-foreclosure ────────────────────────────────────────────────────────────

def _pull_preforeclosure(
    zip_code: str,
    property_type: str,
    max_results: int,
) -> list[dict]:
    """
    GET /preforeclosure/snapshot
    Pulls active pre-foreclosure records (LIS, NOD, or headed-to-auction).
    """
    url     = f"{BASE_URL}/preforeclosure/snapshot"
    results = []
    page    = 1

    while len(results) < max_results:
        params = {
            "postalcode": zip_code,
            "proptype":   property_type,
            "page":       page,
            "pagesize":   min(_PAGE_SIZE, max_results - len(results)),
        }
        data = _get(url, params)
        batch = data.get("property", [])
        if not batch:
            break
        for p in batch:
            lead = _normalize(p, motivation_type="pre_foreclosure")
            if lead:
                results.append(lead)
        if len(batch) < _PAGE_SIZE:
            break  # last page
        page += 1

    return results[:max_results]


# ── Property snapshot (equity / tax-delinquent / vacant) ─────────────────────

def _pull_snapshot(
    zip_code:     str,
    property_type: str,
    max_results:  int,
    extra_params: dict,
    motivation:   str,
) -> list[dict]:
    """
    GET /property/snapshot with caller-supplied filter params.
    Used for high_equity, tax_delinquent, and vacant motivation types.
    """
    url     = f"{BASE_URL}/property/snapshot"
    results = []
    page    = 1

    while len(results) < max_results:
        params = {
            "postalcode": zip_code,
            "proptype":   property_type,
            "page":       page,
            "pagesize":   min(_PAGE_SIZE, max_results - len(results)),
            **extra_params,
        }
        data  = _get(url, params)
        batch = data.get("property", [])
        if not batch:
            break
        for p in batch:
            lead = _normalize(p, motivation_type=motivation)
            if lead:
                results.append(lead)
        if len(batch) < _PAGE_SIZE:
            break
        page += 1

    return results[:max_results]


# ── Equity calculation helper ─────────────────────────────────────────────────

def _calc_equity_pct(prop: dict) -> Optional[float]:
    """
    Estimate equity % from AVM and loan balance.
    equity_pct = ((avm - loan_balance) / avm) * 100
    Returns None if data is missing.
    """
    try:
        avm = (
            prop.get("avm", {}).get("amount", {}).get("value")
            or prop.get("assessment", {}).get("assessed", {}).get("assdttlvalue")
        )
        loan = prop.get("mortgage", {}).get("FirstConcurrent", {}).get("amount")
        if avm and loan and float(avm) > 0:
            return round(((float(avm) - float(loan)) / float(avm)) * 100, 1)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return None


# ── Normalizer ────────────────────────────────────────────────────────────────

def _normalize(prop: dict, motivation_type: str) -> Optional[dict]:
    """
    Map one ATTOM property dict to a flat leads-table-compatible dict.
    Returns None if required fields (address, attom_id) are missing.
    """
    try:
        identifier = prop.get("identifier", {})
        attom_id   = str(identifier.get("attomId") or identifier.get("Id") or "")
        if not attom_id:
            return None

        address_block = prop.get("address", {})
        address       = (
            address_block.get("line1")
            or address_block.get("oneLine", "").split(",")[0].strip()
        )
        if not address:
            return None

        city  = address_block.get("locality", "")
        state = address_block.get("countrySubd", "IN")
        zip_  = address_block.get("postal1", "")

        # Owner info
        owner = prop.get("owner", {})
        owner_name = (
            owner.get("owner1", {}).get("fullname")
            or owner.get("corporateindicator", "")
        )

        return {
            "attom_id":        attom_id,
            "address":         address,
            "city":            city,
            "state":           state,
            "zip":             zip_,
            "owner_name":      owner_name or None,
            "motivation_type": motivation_type,
            "equity_pct":      _calc_equity_pct(prop),
            "list_source":     "list_puller",
            "status":          "new",
        }
    except Exception:
        return None


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _get(url: str, params: dict) -> dict:
    """Execute a GET against ATTOM; raise on HTTP error or ATTOM error code."""
    resp = requests.get(url, headers=_HEADERS, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    data   = resp.json()
    status = data.get("status", {})
    if status.get("code", 0) != 0:
        raise ValueError(f"ATTOM error {status.get('code')}: {status.get('msg')}")
    return data
