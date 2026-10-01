"""
Rent-comps adapter (manual-entry stub — §11 step 6).

Vendor not yet selected (RentRange, Rentometer, Zillow Rent Zestimate, etc.).
The interface is stable; the implementation is swappable per §4 principle 8.

Phase 1 (now): manual_entry() lets the operator supply market rent directly
from a manual comp analysis.  fetch_rent_comps() raises NotImplementedError in
live mode; in dry-run it returns a zero stub so the caller can detect it and
require a manual_entry override before proceeding.

Phase 2 (later): wire a real rental-comp API into fetch_rent_comps() and
retire the manual path once confidence meets the §3 gate thresholds.

FAIL-CLOSED: if neither manual_entry nor a working API returns results, the
underwriter must set rent_confidence="low" and comp_count=0, which fails the
BRRRR eligibility gate — no BRRRR deal proceeds on an unverified rent.

Public API:
    RentCompsResult         — result dataclass
    fetch_rent_comps(...)   -> RentCompsResult   (stub; raises live)
    manual_entry(...)       -> RentCompsResult   (always available)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_SYSTEM_MODE = os.environ.get("SYSTEM_MODE", "live")

# Confidence levels accepted by the eligibility gate
CONFIDENCE_LEVELS = ("low", "medium", "high")


@dataclass(frozen=True)
class RentCompsResult:
    market_rent:    float          # gross monthly rent estimate (blended from comps)
    comp_count:     int            # number of comparable rentals used
    confidence:     str            # "low" | "medium" | "high"
    comps:          list[dict] = field(default_factory=list)  # raw comp records
    source:         str        = "stub"
    notes:          str        = ""

    def __post_init__(self):
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(
                f"Invalid confidence '{self.confidence}'. "
                f"Must be one of {CONFIDENCE_LEVELS}."
            )
        if self.market_rent < 0:
            raise ValueError("market_rent must be non-negative")


def fetch_rent_comps(
    address:       str,
    city:          str,
    state:         str,
    bedrooms:      int,
    bathrooms:     float,
    property_type: str = "SFR",
    radius_miles:  float = 1.0,
    min_comps:     int = 3,
) -> RentCompsResult:
    """
    Fetch rental comps from the configured vendor API.

    Returns a RentCompsResult with market_rent, comp_count, and confidence.

    In dry-run mode: returns a zero stub (caller must detect and require manual_entry).
    In live mode: raises NotImplementedError until a real vendor is wired up.
    """
    if _SYSTEM_MODE == "dry_run":
        log.info(
            "[DRY-RUN][rent_comps] fetch_rent_comps %s %s %s — returning stub",
            address, city, state,
        )
        return RentCompsResult(
            market_rent=0.0,
            comp_count=0,
            confidence="low",
            source="dry_run_stub",
            notes="Dry-run mode — no real rent comps fetched. Use manual_entry() to supply market rent.",
        )

    raise NotImplementedError(
        "Rent-comps vendor not yet configured. "
        "Use adapters.rent_comps.manual_entry() to supply market rent manually, "
        "or wire in a real rental-comp API (RentRange, Rentometer, etc.) in adapters/rent_comps.py."
    )


def manual_entry(
    market_rent:  float,
    comp_count:   int   = 0,
    confidence:   str   = "medium",
    notes:        str   = "",
    comps:        list  = None,
) -> RentCompsResult:
    """
    Supply market rent from a manual comp analysis.

    Use this when the operator has reviewed rental comps from Zillow, Rentometer,
    or the MLS and entered a number directly.  Set confidence and comp_count to
    reflect how thorough the review was so the BRRRR eligibility gate can enforce
    minimum thresholds (min_rent_comps, min_rent_confidence).

    Args:
        market_rent:  Gross monthly rent estimate the operator derived from comps.
        comp_count:   Number of comparable rentals the estimate is based on.
        confidence:   "low" | "medium" | "high" — operator's assessment.
        notes:        Free-text provenance (e.g., "Zillow comps 46201, 3BR/1BA").
        comps:        Optional list of raw comp dicts for the record.

    Returns:
        RentCompsResult with source="manual".
    """
    if market_rent <= 0:
        raise ValueError("market_rent must be positive for a manual entry")
    return RentCompsResult(
        market_rent=market_rent,
        comp_count=comp_count,
        confidence=confidence,
        comps=comps or [],
        source="manual",
        notes=notes,
    )
