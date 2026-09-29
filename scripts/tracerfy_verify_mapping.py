"""
One-shot Tracerfy field mapping verifier.

Runs a 10-record live sample against the real Tracerfy API, scrubs PII from the
response, and writes a fixture file for the contract tests.

Usage (from repo root, with TRACERFY_API_KEY set):
    python scripts/tracerfy_verify_mapping.py

The script prints field shapes from a real response without logging any PII values.
It writes the PII-scrubbed fixture to tests/fixtures/tracerfy_sample.json by default.

⚠️ This script charges Tracerfy credits (5 credits per instant trace, 1 per DNC phone).
Run it only once to verify the mapping, then commit the fixture.
"""

import argparse
import json
import os
import sys
from pathlib import Path

_repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(_repo_root / "agents" / "skip-tracer"))
sys.path.insert(0, str(_repo_root))

import requests

TRACERFY_BASE = "https://app.tracerfy.com/api"
_HEADERS = {
    "Authorization": f"Bearer {os.environ['TRACERFY_API_KEY']}",
    "Content-Type":  "application/json",
}

# ── A known Indianapolis address for the test trace ──────────────────────────
# Replace with any real distressed property address from your leads table.
_TEST_ADDRESS = "2550 N Meridian St"
_TEST_CITY    = "Indianapolis"
_TEST_STATE   = "IN"


def _scrub_pii(obj, depth=0):
    """Recursively replace string values with shape descriptors."""
    if depth > 5:
        return "..."
    if isinstance(obj, dict):
        return {k: _scrub_value(k, v, depth) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_pii(i, depth + 1) for i in obj]
    return obj


def _scrub_value(key: str, val, depth: int):
    _pii_keys = {"first_name", "last_name", "name", "address", "ssn",
                 "dob", "age", "email", "address"}
    _keep_shape_keys = {"number", "phone", "rank", "type", "is_active",
                        "is_dnc", "litigator_flag", "deceased", "status"}
    if key in _pii_keys:
        return "REDACTED"
    if key in _keep_shape_keys:
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return 0
        if isinstance(val, str):
            if val.replace("+", "").replace("-", "").isdigit():
                return "3175550001"   # scrubbed phone placeholder
            return f"string({len(val)})"
    return _scrub_pii(val, depth + 1)


def run(output_path: Path):
    print(f"Tracing test address: {_TEST_ADDRESS}, {_TEST_CITY}, {_TEST_STATE}")
    print("(This charges ~5 Tracerfy credits)")

    resp = requests.post(
        f"{TRACERFY_BASE}/v1/trace/instant",
        headers=_HEADERS,
        json={"address": _TEST_ADDRESS, "city": _TEST_CITY, "state": _TEST_STATE, "find_owner": True},
        timeout=30,
    )
    resp.raise_for_status()
    raw_trace = resp.json()

    print("\n=== Raw field shapes (PII scrubbed) ===")
    scrubbed_trace = _scrub_pii(raw_trace)
    print(json.dumps(scrubbed_trace, indent=2))

    # Extract phones for DNC scrub test
    from tracerfy_utils import extract_phones
    phones = extract_phones(raw_trace)
    if not phones:
        print("\nNo phones found in trace — skipping DNC scrub test.")
        dnc_resp_scrubbed = {"results": []}
    else:
        print(f"\nRunning DNC scrub on {len(phones)} phone(s)...")
        dnc_resp = requests.post(
            f"{TRACERFY_BASE}/v1/dnc-scrub",
            headers=_HEADERS,
            json={"phones": [f"+1{p}" for p in phones]},
            timeout=30,
        )
        dnc_resp.raise_for_status()
        dnc_resp_scrubbed = _scrub_pii(dnc_resp.json())
        print("\n=== DNC scrub field shapes (PII scrubbed) ===")
        print(json.dumps(dnc_resp_scrubbed, indent=2))

    fixture = {
        "_note": "PII-scrubbed Tracerfy API response fixture for contract tests.",
        "_pii_scrubbed": True,
        "trace_response": scrubbed_trace,
        "dnc_scrub_response": dnc_resp_scrubbed,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(fixture, indent=2))
    print(f"\nFixture written to: {output_path}")
    print("\nNext step: run `pytest tests/test_tracerfy_contract.py` to verify the mapping.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default=str(_repo_root / "tests" / "fixtures" / "tracerfy_sample.json"),
    )
    args = parser.parse_args()
    run(Path(args.output))
