"""
Google Street View Static API client for the ARV/MAO agent.

Protocol (in order — no billed calls unless coverage confirmed and fresh):
  1. GET metadata endpoint (free, no charge).
  2. If status != 'OK' -> no coverage, return early.
  3. Parse capture date; if older than MAX_AGE_YEARS -> stale, return early.
  4. Only then fetch the actual image (billed call).

Env var required: GOOGLE_MAPS_API_KEY
  Must have Street View Static API enabled on the GCP project.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

_API_KEY    = os.environ.get("GOOGLE_MAPS_API_KEY", "")
_META_URL   = "https://maps.googleapis.com/maps/api/streetview/metadata"
_IMAGE_URL  = "https://maps.googleapis.com/maps/api/streetview"
_TIMEOUT    = 15          # seconds
MAX_AGE_YEARS = 3


def _location_str(address: str, city: str, state: str, zip_code: str) -> str:
    return f"{address}, {city}, {state} {zip_code}"


def check_coverage(
    address: str,
    city: str,
    state: str,
    zip_code: str,
) -> dict:
    """
    Check Street View metadata without incurring an image charge.

    Returns:
      has_coverage  : bool
      capture_date  : date | None
      is_stale      : bool   (True when image older than MAX_AGE_YEARS)
      reason        : str    human-readable explanation
    """
    if not _API_KEY:
        return {"has_coverage": False, "capture_date": None,
                "is_stale": False, "reason": "GOOGLE_MAPS_API_KEY not configured"}

    location = _location_str(address, city, state, zip_code)
    try:
        resp = requests.get(
            _META_URL,
            params={"location": location, "key": _API_KEY},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("Street View metadata failed: %s", exc)
        return {"has_coverage": False, "capture_date": None,
                "is_stale": False, "reason": f"metadata request failed: {exc}"}

    if data.get("status") != "OK":
        return {"has_coverage": False, "capture_date": None,
                "is_stale": False, "reason": f"no coverage (status={data.get('status')})"}

    # date field is "YYYY-MM" or "YYYY-MM-DD"
    raw_date = data.get("date", "")
    capture_date: date | None = None
    if raw_date:
        try:
            # normalise to full date string
            if len(raw_date) == 7:      # YYYY-MM
                raw_date += "-01"
            capture_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    is_stale = False
    reason = "coverage found"
    if capture_date:
        age_years = (date.today() - capture_date).days / 365.25
        if age_years > MAX_AGE_YEARS:
            is_stale = True
            reason = f"image stale ({capture_date}, {age_years:.1f}yrs old)"
        else:
            reason = f"coverage found, captured {capture_date}"

    return {
        "has_coverage": True,
        "capture_date": capture_date,
        "is_stale":     is_stale,
        "reason":       reason,
    }


def fetch_image(
    address: str,
    city: str,
    state: str,
    zip_code: str,
    width: int = 640,
    height: int = 480,
) -> bytes | None:
    """
    Fetch the Street View image bytes. Caller MUST check coverage first
    (check_coverage) and only call this when has_coverage=True and is_stale=False.

    Returns image bytes, or None on any error.
    """
    if not _API_KEY:
        return None

    location = _location_str(address, city, state, zip_code)
    try:
        resp = requests.get(
            _IMAGE_URL,
            params={
                "size":     f"{width}x{height}",
                "location": location,
                "fov":      90,
                "pitch":    0,
                "key":      _API_KEY,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        if resp.headers.get("content-type", "").startswith("image/"):
            return resp.content
        log.warning("Street View image response was not an image: %s",
                    resp.headers.get("content-type"))
        return None
    except Exception as exc:
        log.warning("Street View image fetch failed: %s", exc)
        return None
