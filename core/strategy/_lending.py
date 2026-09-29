"""
lru_cache loader for config/lending.yaml (acquisition + refi lender profiles).

Tests bypass file I/O by passing lenders= directly to compute_brrrr().
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_LENDING_PATH = Path(__file__).parent.parent.parent / "config" / "lending.yaml"


@lru_cache(maxsize=1)
def load() -> dict:
    return yaml.safe_load(_LENDING_PATH.read_text())


def invalidate_cache() -> None:
    load.cache_clear()


def get_acq_lender(lenders: dict, lender_id: str = "hm_default") -> dict:
    for p in lenders.get("acquisition_lenders", []):
        if p["id"] == lender_id:
            return p
    raise ValueError(f"Acquisition lender {lender_id!r} not found in lending config")


def get_refi_lender(lenders: dict, lender_id: str = "dscr_default") -> dict:
    for p in lenders.get("refi_lenders", []):
        if p["id"] == lender_id:
            return p
    raise ValueError(f"Refi lender {lender_id!r} not found in lending config")
