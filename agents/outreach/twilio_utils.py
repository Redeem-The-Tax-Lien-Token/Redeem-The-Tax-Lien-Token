"""
Twilio SMS helpers for the Outreach Agent.
Handles sending, TCPA quiet-hours enforcement, and message construction.

⚠️ COMPLIANCE: Uses A2P 10DLC Messaging Service (TWILIO_MESSAGING_SERVICE_SID),
never a direct From number. Every seller-facing message must go through
build_seller_sms() from shared.compliance.disclosures before being passed here.
"""

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from twilio.rest import Client

log = logging.getLogger("outreach.twilio")

TWILIO_ACCOUNT_SID          = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN           = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_MESSAGING_SERVICE_SID = os.environ["TWILIO_MESSAGING_SERVICE_SID"]

_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

_INDY_TZ    = ZoneInfo("America/Indiana/Indianapolis")
_HOUR_START = 8
_HOUR_END   = 21

# ── SMS body templates (no disclosure, no opt-out — injected by build_seller_sms) ──
# Keys: touch number 1-3. Tokens: {owner_name}, {address}, {city}.
# {owner_name} falls back to "there" when unknown.
SMS_TEMPLATES: dict[int, str] = {
    1: (
        "Hi {owner_name}, I'm a local cash buyer interested in your property "
        "at {address}, {city}. No repairs, no agent fees, fast close. "
        "Would you consider a cash offer?"
    ),
    2: (
        "Hi {owner_name}, following up on {address}. I can close in as little "
        "as 2 weeks — no repairs or showings needed. Still open to a cash offer?"
    ),
    3: (
        "Last message about {address}. If the timing isn't right, no worries — "
        "I understand. If you ever want a cash offer, feel free to reach out."
    ),
}


def twilio_startup_check() -> None:
    """
    Verify the Messaging Service SID exists and is reachable.
    Raises RuntimeError (fail closed) if the check fails — the agent will not start.
    Never logs the credential values.
    """
    try:
        svc = _client.messaging.v1.services(TWILIO_MESSAGING_SERVICE_SID).fetch()
        log.info(
            "Twilio self-check passed: MessagingService sid=%s friendly_name=%s",
            svc.sid, svc.friendly_name,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Twilio self-check FAILED for TWILIO_MESSAGING_SERVICE_SID — "
            f"outreach agent cannot start. Error: {exc}. "
            "Verify the SID in Replit Secrets and that the A2P 10DLC campaign is active."
        ) from exc


def is_within_calling_hours() -> bool:
    """Return True if the current Indianapolis time is between 8 AM and 9 PM."""
    now = datetime.now(_INDY_TZ)
    return _HOUR_START <= now.hour < _HOUR_END


def build_message(touch_number: int, owner_name: str | None, address: str, city: str) -> str:
    """
    Render the raw SMS body (no disclosure, no opt-out).
    The caller must pass the result through build_seller_sms() before sending.
    """
    template = SMS_TEMPLATES.get(touch_number, SMS_TEMPLATES[1])
    return template.format(
        owner_name = owner_name.split()[0] if owner_name else "there",
        address    = address,
        city       = city or "Indianapolis",
    )


def send_sms(to_number: str, message: str) -> str:
    """
    Send one SMS via the A2P 10DLC Messaging Service.
    Returns the Twilio message SID.
    Raises twilio.base.exceptions.TwilioRestException on failure.
    """
    msg = _client.messages.create(
        body                  = message,
        messaging_service_sid = TWILIO_MESSAGING_SERVICE_SID,
        to                    = to_number,
    )
    return msg.sid
