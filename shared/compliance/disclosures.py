"""
Statutory disclosure and opt-out language.
Indiana HB 1068 / IC 32-21-16.5 wholesaler disclosure + TCPA opt-out footer.

⚠️ COMPLIANCE: Disclosures are inserted by code only — never written, paraphrased,
or omitted by the LLM (CLAUDE.md §2.1).

Public API for seller-facing messages:
    text, version = build_seller_sms(body)
    # text has disclosure prepended and opt-out footer appended
    # version is the string to store in outreach_log.disclosure_version
"""

import re
from functools import lru_cache
from pathlib import Path

# TCPA — required on every outbound SMS campaign message.
TCPA_OPT_OUT_FOOTER = "Reply STOP to unsubscribe."

# No messages before 8 AM or after 9 PM in recipient's LOCAL time zone (TCPA).
TCPA_QUIET_HOURS = {"start": "08:00", "end": "21:00"}

# A2P 10DLC campaign use-case string (matches Twilio TCR registration).
SMS_CAMPAIGN_USE_CASE = "Real Estate - Motivated Seller Outreach"

# Indiana HB 1068 — included in every PSA (contract-level; not the solicitation text).
INDIANA_HB1068_DISCLOSURE = (
    "Buyer is a real estate investor who may assign this contract to a third party. "
    "Buyer does not intend to occupy the property. The purchase price may be below "
    "market value. Seller acknowledges this disclosure."
)

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "hb1068_solicitation.txt"


@lru_cache(maxsize=1)
def load_solicitation_disclosure() -> tuple[str, str]:
    """
    Load the versioned HB 1068 solicitation disclosure text from the template file.
    Returns (disclosure_text, version_string).

    Raises RuntimeError if the file is missing (fail closed — no sends without it).
    Cached after first call; restart the process to reload a new version.
    """
    if not _TEMPLATE_PATH.exists():
        raise RuntimeError(
            f"HB 1068 solicitation template not found: {_TEMPLATE_PATH}. "
            "Cannot send seller-facing messages without it (fail closed)."
        )
    raw = _TEMPLATE_PATH.read_text(encoding="utf-8")

    version_match = re.search(r"^# version:\s*(.+)$", raw, re.MULTILINE)
    if not version_match:
        raise RuntimeError(f"HB 1068 template has no '# version:' header: {_TEMPLATE_PATH}")
    version = version_match.group(1).strip()

    sep = "---\n"
    if sep not in raw:
        raise RuntimeError(f"HB 1068 template missing '---' separator: {_TEMPLATE_PATH}")
    disclosure_text = raw.split(sep, 1)[1].strip()
    if not disclosure_text:
        raise RuntimeError(f"HB 1068 template has empty disclosure text: {_TEMPLATE_PATH}")

    return disclosure_text, version


def build_seller_sms(body: str) -> tuple[str, str]:
    """
    Build a compliant seller-facing SMS message.

    Prepends the HB 1068 solicitation disclosure and appends the TCPA opt-out
    footer. Never calls the LLM. The disclosure text comes from the versioned
    template file only.

    Returns:
        (full_message, disclosure_version)

    The caller must store disclosure_version in outreach_log.disclosure_version.
    Raises RuntimeError if the template file is missing or malformed (fail closed).
    """
    disclosure_text, version = load_solicitation_disclosure()
    full_message = f"{disclosure_text} {body}"
    if TCPA_OPT_OUT_FOOTER not in full_message:
        full_message = f"{full_message} {TCPA_OPT_OUT_FOOTER}"
    return full_message, version


def build_sms_message(body: str, include_opt_out: bool = True) -> str:
    """Legacy helper — appends TCPA opt-out footer only. Does NOT include the
    HB 1068 solicitation disclosure. Use build_seller_sms() for all seller-facing
    messages (outreach, nurture, offer). This function is retained for non-seller
    messages (e.g., internal alerts, buyer blasts that don't require §2.1 disclosure).
    """
    if include_opt_out and TCPA_OPT_OUT_FOOTER not in body:
        return f"{body} {TCPA_OPT_OUT_FOOTER}"
    return body
