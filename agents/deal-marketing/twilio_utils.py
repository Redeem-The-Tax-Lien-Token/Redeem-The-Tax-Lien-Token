"""
Twilio helpers for the Deal Marketing Agent.
Handles buyer blast SMS and TCPA quiet-hours enforcement.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from twilio.rest import Client

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM_NUMBER = os.environ["TWILIO_FROM_NUMBER"]

_client  = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
_INDY_TZ = ZoneInfo("America/Indiana/Indianapolis")

_OPT_OUT      = "Reply STOP to opt out."
_HOUR_START   = 8
_HOUR_END     = 21


def is_within_calling_hours() -> bool:
    now = datetime.now(_INDY_TZ)
    return _HOUR_START <= now.hour < _HOUR_END


def build_deal_blast(
    zip_code:        str,
    city:            str,
    arv_mid:         float,
    repair_estimate: float,
    offer_amount:    float,
    assignment_fee:  float,
    beds:            int | None = None,
    baths:           int | None = None,
    arv_confidence:  str = "medium",
) -> str:
    """
    Build a concise buyer-blast SMS.
    Does NOT include the street address — buyers who reply YES get it directly
    to comply with Indiana HB 1068 private-list marketing rules (§ 7.3).
    """
    size_str = ""
    if beds and baths:
        size_str = f"{beds}BR/{baths}BA SFR | "
    elif beds:
        size_str = f"{beds}BR SFR | "

    conf_note = f" ({arv_confidence} conf)" if arv_confidence != "high" else ""

    return (
        f"DEAL ALERT - {city} {zip_code}: "
        f"{size_str}"
        f"ARV ~${arv_mid:,.0f}{conf_note} | "
        f"Repairs ~${repair_estimate:,.0f} | "
        f"Price ${offer_amount:,.0f} | "
        f"Fee ${assignment_fee:,.0f} | "
        f"Reply YES for address & details. {_OPT_OUT}"
    )


def send_sms(to_number: str, message: str) -> str:
    """Send one SMS; returns Twilio message SID."""
    msg = _client.messages.create(
        body  = message,
        from_ = TWILIO_FROM_NUMBER,
        to    = to_number,
    )
    return msg.sid
