"""
Pytest wrapper for the wholesale golden deal eval set.

CI FAILS if any golden deal's outcome or numbers change without an
approved config change.  Do not skip or xfail these tests.

To re-baseline after an intentional config change:
  1. Update the affected JSON file in evals/deals/.
  2. Update expected values to match the new computation.
  3. Get operator + underwriting-reviewer sign-off on the change.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from evals.run_golden_deals import _DEALS_DIR, run_deal

_DEAL_FILES = sorted(_DEALS_DIR.glob("*.json"))


@pytest.mark.parametrize("deal_path", _DEAL_FILES, ids=lambda p: p.stem)
def test_golden_deal(deal_path: Path):
    deal = json.loads(deal_path.read_text())
    failures = run_deal(deal)
    assert not failures, "\n".join(failures)
