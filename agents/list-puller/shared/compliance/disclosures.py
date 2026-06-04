"""
Statutory disclosure and opt-out language.
Indiana HB 1068 (wholesaler disclosure) + TCPA opt-out footer.
All SMS messages must pass through build_sms_message() before being sent.
"""

# Indiana HB 1068 — must appear in every PSA presented to a seller.
INDIANA_HB1068_DISCLOSURE = (
    "Buyer is a real estate investor who may assign this contract to a third party. "
    "Buyer does not intend to occupy the property. The purchase price may be below "
    "market value. Seller acknowledges this disclosure."
)

# TCPA — required on every outbound SMS campaign message.
TCPA_OPT_OUT_FOOTER = "Reply STOP to unsubscribe."

# No messages before 8 AM or after 9 PM in recipient's LOCAL time zone (TCPA).
TCPA_QUIET_HOURS = {"start": "08:00", "end": "21:00"}

# A2P 10DLC campaign use-case string (matches Twilio TCR registration).
SMS_CAMPAIGN_USE_CASE = "Real Estate - Motivated Seller Outreach"


def build_sms_message(body: str, include_opt_out: bool = True) -> str:
    """Append TCPA opt-out footer if not already present."""
    if include_opt_out and TCPA_OPT_OUT_FOOTER not in body:
        return f"{body} {TCPA_OPT_OUT_FOOTER}"
    return body
