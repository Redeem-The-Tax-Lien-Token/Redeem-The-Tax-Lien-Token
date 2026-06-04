"""
Twilio SMS helpers for the Outreach Agent.
Handles sending, TCPA quiet-hours enforcement, and message construction.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from twilio.rest import Client

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM_NUMBER = os.environ["TWILIO_FROM_NUMBER"]

_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Indianapolis stays on Eastern Time year-round (no DST change for most of Indiana)
_INDY_TZ = ZoneInfo("America/Indiana/Indianapolis")

# TCPA quiet hours: no messages before 8 AM or after 9 PM local time (§ 6.5)
_HOUR_START = 8
_HOUR_END   = 21

# Required opt-out footer on every outbound SMS (§ 6.5)
_OPT_OUT = "Reply STOP to unsubscribe."

# ── Multi-touch cadence templates ─────────────────────────────────────────────
# Personalization tokens: {owner_name}, {address}, {city}
# {owner_name} falls back to "there" if unknown.
SMS_TEMPLATES: dict[int, str] = {
    1: (
        "Hi {owner_name}, I'm a local cash buyer interested in your property "
        "at {address}, {city}. No repairs, no agent fees, fast close. "
        "Would you consider a cash offer? {opt_out}"
    ),
    2: (
        "Hi {owner_name}, following up on {address}. I can close in as little "
        "as 2 weeks — no repairs or showings needed. Still open to a cash offer? "
        "{opt_out}"
    ),
    3: (
        "Last message about {address}. If the timing isn't right, no worries — "
        "I understand. If you ever want a cash offer, feel free to reach out. "
        "{opt_out}"
    ),
}


def is_within_calling_hours() -> bool:
    """Return True if the current Indianapolis time is between 8 AM and 9 PM."""
    now = datetime.now(_INDY_TZ)
    return _HOUR_START <= now.hour < _HOUR_END


def build_message(touch_number: int, owner_name: str | None, address: str, city: str) -> str:
    """
    Render the SMS template for the given touch number.
    owner_name falls back to 'there' if None.
    """
    template = SMS_TEMPLATES.get(touch_number, SMS_TEMPLATES[1])
    return template.format(
        owner_name = owner_name.split()[0] if owner_name else "there",
        address    = address,
        city       = city or "Indianapolis",
        opt_out    = _OPT_OUT,
    )


def send_sms(to_number: str, message: str) -> str:
    """
    Send one SMS via Twilio. Returns the Twilio message SID.
    Raises twilio.base.exceptions.TwilioRestException on failure.
    """
    msg = _client.messages.create(
        body  = message,
        from_ = TWILIO_FROM_NUMBER,
        to    = to_number,
    )
    return msg.sid
