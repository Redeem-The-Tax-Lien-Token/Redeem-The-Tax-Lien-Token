"""
Listing syndication adapter (stub).

Syndicates rental listings to platforms (Zillow, Apartments.com, etc.) for
properties the entity OWNS.  Never used to market a property for sale that
the entity does not own — that would violate §2.1.

Public API:
    post_listing(listing) -> SyndicationResult
    update_listing(listing_id, updates) -> SyndicationResult
    remove_listing(listing_id) -> None
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_SYSTEM_MODE = os.environ.get("SYSTEM_MODE", "live")


@dataclass(frozen=True)
class SyndicationResult:
    listing_id:  str
    platform:    str
    status:      str   # 'posted' | 'updated' | 'dry_run'
    url:         str   = ""
    notes:       str   = ""


def post_listing(listing: dict) -> SyndicationResult:
    """Post a rental listing to the syndication platform."""
    if _SYSTEM_MODE == "dry_run":
        log.info("[DRY-RUN][syndication] post_listing address=%s", listing.get("address"))
        return SyndicationResult(
            listing_id=f"dry_run_{listing.get('deal_id', 0)}",
            platform="dry_run",
            status="dry_run",
            notes="Dry-run mode — not posted to any platform",
        )
    raise NotImplementedError(
        "Listing syndication adapter is a stub. Wire in a real platform before going live."
    )


def update_listing(listing_id: str, updates: dict) -> SyndicationResult:
    if _SYSTEM_MODE == "dry_run":
        log.info("[DRY-RUN][syndication] update_listing id=%s", listing_id)
        return SyndicationResult(listing_id=listing_id, platform="dry_run", status="dry_run")
    raise NotImplementedError("Listing syndication adapter is a stub.")


def remove_listing(listing_id: str) -> None:
    if _SYSTEM_MODE == "dry_run":
        log.info("[DRY-RUN][syndication] remove_listing id=%s", listing_id)
        return
    raise NotImplementedError("Listing syndication adapter is a stub.")
