"""
ATTOM API helpers for the ARV/MAO agent.
Fetches sales comparables and property detail. Auto-expands radius/date range
if fewer than 3 comps are returned (per ARV methodology in master-context § 2.4).
"""

import os
from urllib.parse import quote

import requests

ATTOM_API_KEY = os.environ["ATTOM_API_KEY"]
BASE_URL = "https://api.gateway.attomdata.com/propertyapi/v1.0.0"
V2_URL = "https://api.gateway.attomdata.com/property/v2"

_HEADERS = {
    "apikey": ATTOM_API_KEY,
    "Accept": "application/json",
}

_TIMEOUT = 30  # seconds


def get_comps(
    address: str,
    city: str,
    state: str,
    zip_code: str,
    radius: float = 0.5,
    months: int = 6,
    max_comps: int = 10,
) -> list[dict]:
    """
    Return normalized comp dicts from ATTOM SalesComparables.
    Expansion ladder (§ 2.4):
      1st try : 0.5 mi / 6 mo
      2nd try : 1.0 mi / 6 mo  (if < 3 comps)
      3rd try : 1.0 mi / 12 mo (if still < 3 comps)
    """
    def _fetch(r: float, m: int) -> list[dict]:
        url = (
            f"{V2_URL}/SalesComparables/Address"
            f"/{quote(address, safe='')}"
            f"/{quote(city, safe='')}"
            f"/-/{state}/{zip_code}"
        )
        params = {
            "searchType": "Radius",
            "miles": r,
            "saleDateRange": m,
            "maxComps": max_comps,
            "ownerOccupied": "Both",
            "distressed": "IncludeDistressed",
        }
        resp = requests.get(url, headers=_HEADERS, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        # ATTOM embeds a status code in the body; 0 = success
        status = data.get("status", {})
        if status.get("code", 0) != 0:
            raise ValueError(f"ATTOM status error: {status.get('msg', 'unknown')}")

        return _parse_comps(data)

    comps = _fetch(radius, months)
    if len(comps) < 3:
        comps = _fetch(1.0, months)
    if len(comps) < 3:
        comps = _fetch(1.0, 12)
    return comps


def _parse_comps(data: dict) -> list[dict]:
    """Normalize ATTOM SalesComparables response into flat dicts."""
    results = []
    for c in data.get("comparables", []):
        try:
            building = c.get("building", {})
            size     = building.get("size", {})
            rooms    = building.get("rooms", {})
            summary  = c.get("summary", {})
            loc      = c.get("location", {})
            addr     = c.get("address", {})
            sale     = c.get("sale", {})
            sale_amt = c.get("sale", {})

            # sale amount — nested under sale.amount.saleamt
            sale_price = (
                sale.get("amount", {}).get("saleamt")
                or sale.get("saleamt")
            )
            # sale date — salesearchdate preferred, fall back to salerecdate
            sale_date = (
                sale.get("salesearchdate")
                or sale.get("amount", {}).get("salerecdate")
                or ""
            )
            sqft = size.get("universalsize") or size.get("livingsize")
            distance = loc.get("distance") or c.get("proximity", {}).get("miles", 0)

            if not sale_price or not sqft:
                continue

            results.append({
                "address":         addr.get("line1") or addr.get("oneLine", ""),
                "city":            addr.get("locality", ""),
                "distance_miles":  round(float(distance), 2),
                "sale_price":      int(sale_price),
                "sale_date":       sale_date,
                "sqft":            int(sqft),
                "beds":            rooms.get("beds"),
                "baths":           rooms.get("bathstotal"),
                "year_built":      summary.get("yearbuilt"),
                "price_per_sqft":  round(sale_price / sqft, 2),
                "property_type":   summary.get("proptype", ""),
            })
        except (KeyError, TypeError, ZeroDivisionError, ValueError):
            continue
    return results


def get_property_detail(address1: str, address2: str) -> dict | None:
    """
    Fetch full property detail from ATTOM.
    address1 = street address, address2 = "City, ST Zip"
    Returns the first property dict or None.
    """
    url = f"{BASE_URL}/property/detail"
    params = {"address1": address1, "address2": address2}
    resp = requests.get(url, headers=_HEADERS, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    props = data.get("property", [])
    return props[0] if props else None
