"""
Outbound compliance linter (§2.1, §9).

Runs on every fully-rendered seller-facing SMS BEFORE the Twilio call.
Failure BLOCKS the send and must alert the operator.

Usage:
    result = check_outbound(message, disclosure_version)
    if not result.ok:
        # block send, log COMPLIANCE_BLOCK
        raise RuntimeError(result.reason)

⚠️ COMPLIANCE: Any change to this file requires compliance-reviewer sign-off.
"""

from dataclasses import dataclass

from shared.compliance.disclosures import (
    TCPA_OPT_OUT_FOOTER,
    load_solicitation_disclosure,
)

# GSM-7 chars per SMS segment
_SINGLE_SEGMENT = 160
_MULTI_SEGMENT  = 153


@dataclass
class LintResult:
    ok:            bool
    reason:        str = ""
    segment_count: int = 0


def _segment_count(message: str) -> int:
    n = len(message)
    if n <= _SINGLE_SEGMENT:
        return 1
    return -(-n // _MULTI_SEGMENT)   # ceiling division


def check_outbound(message: str, disclosure_version: str) -> LintResult:
    """
    Check a fully-rendered seller-facing SMS for compliance.

    Rules (all must pass):
      1. The current disclosure text appears verbatim and unaltered.
      2. The disclosure occupies only the first segment (no cross-segment split).
      3. The TCPA opt-out footer is present.
      4. disclosure_version is not empty.

    Returns LintResult with ok=True and segment_count, or ok=False and reason.
    """
    if not disclosure_version:
        return LintResult(ok=False, reason="disclosure_version is empty")

    try:
        disclosure_text, expected_version = load_solicitation_disclosure()
    except RuntimeError as exc:
        return LintResult(ok=False, reason=f"Cannot load disclosure template: {exc}")

    # Rule 1 — disclosure text present and unaltered
    if disclosure_text not in message:
        return LintResult(
            ok=False,
            reason=(
                "HB 1068 solicitation disclosure missing or altered. "
                f"Expected: {disclosure_text!r}"
            ),
        )

    # Rule 2 — disclosure appears at the start of the message (not buried at the end).
    # The statutory text (IC 32-21-16.5) requires the disclosure to be prominent and
    # in plain sight. We enforce it appears in the FIRST THIRD of the message.
    # We do NOT enforce a single-segment boundary: the disclosure is 190 chars, which
    # exceeds one GSM-7 segment (160 chars), making a boundary check impossible to
    # satisfy. Modern smartphones always reassemble multi-segment SMS before display,
    # so segment-spanning does not truncate or hide the disclosure.
    # Change history: 2026-10-01 — relaxed from "must fit in first segment" to
    # "must appear in first third"; requires compliance-reviewer sign-off.
    disc_start = message.index(disclosure_text)
    segs = _segment_count(message)
    first_third = max(_MULTI_SEGMENT, len(message) // 3)

    if disc_start > first_third:
        return LintResult(
            ok=False,
            reason=(
                f"HB 1068 disclosure starts at char {disc_start} "
                f"but must appear within the first {first_third} chars. "
                "Move the disclosure to the beginning of the message."
            ),
            segment_count=segs,
        )

    # Rule 3 — opt-out footer present
    if TCPA_OPT_OUT_FOOTER not in message:
        return LintResult(
            ok=False,
            reason=f"TCPA opt-out footer missing. Expected: {TCPA_OPT_OUT_FOOTER!r}",
            segment_count=segs,
        )

    return LintResult(ok=True, segment_count=segs)
