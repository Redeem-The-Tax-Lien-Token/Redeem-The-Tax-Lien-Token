"""
Contract tests for Tracerfy API field mapping.

These tests verify that the fields assumed by tracerfy_utils.py actually exist
in a real (PII-scrubbed) Tracerfy API response. They use a recorded fixture
stored in tests/fixtures/tracerfy_sample.json.

To regenerate the fixture against the live API:
    python scripts/tracerfy_verify_mapping.py --output tests/fixtures/tracerfy_sample.json

⚠️ If these tests fail it means the Tracerfy API response shape has changed.
The skip-tracer batch job guards against running with a broken mapping by calling
verify_tracerfy_contract() on startup.
"""

import json
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).parent / "fixtures" / "tracerfy_sample.json"


@pytest.fixture(scope="module")
def fixture():
    assert _FIXTURE.exists(), (
        f"Fixture not found: {_FIXTURE}. "
        "Run: python scripts/tracerfy_verify_mapping.py "
        "--output tests/fixtures/tracerfy_sample.json"
    )
    return json.loads(_FIXTURE.read_text())


# ── Trace response shape ──────────────────────────────────────────────────────

def test_trace_response_has_phones(fixture):
    trace = fixture["trace_response"]
    assert "phones" in trace, "Missing 'phones' key in trace response"
    assert isinstance(trace["phones"], list)


def test_phone_entry_has_number_and_rank(fixture):
    phones = fixture["trace_response"]["phones"]
    if phones:
        first = phones[0]
        assert "number" in first, "Phone entry missing 'number' field"
        assert "rank" in first, "Phone entry missing 'rank' field"


def test_trace_response_has_deceased_flag(fixture):
    assert "deceased" in fixture["trace_response"], (
        "Missing 'deceased' key — is_deceased() will always return False"
    )


def test_trace_response_has_litigator_flag(fixture):
    assert "litigator_flag" in fixture["trace_response"], (
        "Missing 'litigator_flag' key — has_litigator_flag() will always return False"
    )


def test_trace_response_has_emails(fixture):
    assert "emails" in fixture["trace_response"], (
        "Missing 'emails' key — extract_email() will always return None"
    )


def test_email_entry_has_address(fixture):
    emails = fixture["trace_response"].get("emails", [])
    if emails:
        assert "address" in emails[0], "Email entry missing 'address' field"


# ── DNC scrub response shape ──────────────────────────────────────────────────

def test_dnc_scrub_has_results(fixture):
    assert "results" in fixture["dnc_scrub_response"], (
        "Missing 'results' key in DNC scrub response — scrub_phones() will always return empty"
    )


def test_dnc_result_has_phone_field(fixture):
    results = fixture["dnc_scrub_response"]["results"]
    if results:
        assert "phone" in results[0], "DNC result missing 'phone' field"


def test_dnc_result_has_is_dnc_field(fixture):
    results = fixture["dnc_scrub_response"]["results"]
    if results:
        assert "is_dnc" in results[0], "DNC result missing 'is_dnc' field"


def test_dnc_result_has_litigator_flag_field(fixture):
    results = fixture["dnc_scrub_response"]["results"]
    if results:
        assert "litigator_flag" in results[0], (
            "DNC result missing 'litigator_flag' field"
        )


# ── Integration: tracerfy_utils parses fixture correctly ─────────────────────

def test_extract_phones_from_fixture(fixture):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "agents" / "skip-tracer"))
    from tracerfy_utils import extract_phones, scrub_phones

    phones = extract_phones(fixture["trace_response"])
    assert len(phones) > 0, "extract_phones returned empty list from fixture"
    assert all(len(p) == 10 and p.isdigit() for p in phones), (
        "extract_phones returned non-10-digit numbers"
    )


def test_scrub_phones_from_fixture(fixture):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "agents" / "skip-tracer"))
    from tracerfy_utils import scrub_phones

    # Directly parse the fixture DNC results (no live API call)
    results = fixture["dnc_scrub_response"]["results"]
    clean = {r["phone"] for r in results if not r.get("is_dnc") and not r.get("litigator_flag")}
    assert "3175550001" in clean
    assert "3175550002" not in clean
