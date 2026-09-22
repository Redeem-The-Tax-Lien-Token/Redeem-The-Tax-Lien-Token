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

    # Rule 2 — disclosure does not cross a segment boundary
    disc_start = message.index(disclosure_text)
    disc_end   = disc_start + len(disclosure_text)
    segs = _segment_count(message)

    if segs > 1:
        first_seg_end = _MULTI_SEGMENT
        if disc_end > first_seg_end:
            return LintResult(
                ok=False,
                reason=(
                    f"HB 1068 disclosure crosses segment boundary "
                    f"(ends at char {disc_end}, first segment ends at {first_seg_end}). "
                    "Shorten the message body."
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
