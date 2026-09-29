"""
Strategy config loader — reads config/strategy.yaml once per process.

All strategy modules call load() to get the config dict.
Pass cfg= explicitly in tests to override without touching the file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"


@lru_cache(maxsize=1)
def load() -> dict:
    """Return the parsed strategy.yaml dict. Cached after first read."""
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Strategy config not found: {_CONFIG_PATH}. "
            "Copy config/strategy.yaml from the repo root."
        )
    return yaml.safe_load(_CONFIG_PATH.read_text())


def invalidate_cache() -> None:
    """Clear the lru_cache — use in tests that patch the config file."""
    load.cache_clear()
