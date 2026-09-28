"""
Nurture message templates for Agent 9.

Templates are keyed by (status_bucket, touch_number).  The caller passes
the raw body through build_seller_sms() (shared.compliance.disclosures)
before any send — the HB 1068 disclosure and TCPA opt-out are injected
there, never here.

Rules (§2.1, §2 universal):
  - Never mention a specific price or counter a seller's price.
  - Never imply negotiation ("we can go higher", "make us an offer").
  - Never imply the operator is a licensed agent or broker.
  - Touch 1 is re-engagement; later touches shift to light check-ins.
  - After touch 12 (cycle boundary), messages become lower-frequency prompts.
"""

from __future__ import annotations

# Status buckets for template selection
WARM_BUCKET          = "warm"
COLD_BUCKET          = "cold"
NURTURE_BUCKET       = "nurture"
OFFER_DECLINED_BUCKET = "offer_declined"

# Tokens: {first_name}, {address}, {city}
# first_name falls back to "there" when unknown.

_TEMPLATES: dict[tuple[str, int], str] = {
    # ── Warm bucket (expressed some interest but not HOT) ─────────────────────
    (WARM_BUCKET, 1): (
        "Hi {first_name}, just checking in about {address}. "
        "We're still interested in a cash offer — no repairs, no agents, fast close. "
        "Has anything changed with your situation?"
    ),
    (WARM_BUCKET, 2): (
        "Hi {first_name}, following up on {address} in {city}. "
        "Our offer is still open — we can close on your timeline. "
        "Let us know if you'd like to revisit."
    ),
    (WARM_BUCKET, 3): (
        "Hi {first_name}, hope all is well. We're still a cash buyer "
        "interested in {address}. No pressure — just wanted to stay in touch "
        "in case the timing works better now."
    ),

    # ── Cold bucket ────────────────────────────────────────────────────────────
    (COLD_BUCKET, 1): (
        "Hi {first_name}, we're a cash buyer in {city} and we had reached out "
        "about {address}. We're still actively looking in the area — "
        "would you consider a quick cash offer?"
    ),
    (COLD_BUCKET, 2): (
        "Hi {first_name}, quick follow-up about {address}. "
        "We close fast, pay cash, and handle all the paperwork. "
        "Is now a better time to chat?"
    ),
    (COLD_BUCKET, 3): (
        "Hi {first_name}, last check-in about {address}. "
        "If the timing isn't right now, no worries — we'd love to hear from you "
        "if that changes. Take care."
    ),

    # ── Nurture bucket (general long-cycle) ────────────────────────────────────
    (NURTURE_BUCKET, 1): (
        "Hi {first_name}, we're still a cash buyer interested in {address}. "
        "Happy to work around your timeline — even if you're months out, "
        "we can talk now and close when you're ready."
    ),
    (NURTURE_BUCKET, 2): (
        "Hi {first_name}, just a quick check-in about {address}. "
        "We buy as-is for cash — no repairs or showings needed. "
        "Let us know if you'd like to revisit."
    ),
    (NURTURE_BUCKET, 3): (
        "Hi {first_name}, hope things are going well. "
        "We're still interested in {address} whenever the timing makes sense. "
        "No pressure — just staying in touch."
    ),

    # ── Offer declined bucket ──────────────────────────────────────────────────
    (OFFER_DECLINED_BUCKET, 1): (
        "Hi {first_name}, thanks for considering our offer on {address}. "
        "We understand it wasn't the right fit right now. "
        "If your situation changes, we'd be happy to revisit — just reach out."
    ),
    (OFFER_DECLINED_BUCKET, 2): (
        "Hi {first_name}, following up about {address}. "
        "We're still buying in {city} and would love to talk if the timing works better now."
    ),
    (OFFER_DECLINED_BUCKET, 3): (
        "Hi {first_name}, last check-in about {address}. "
        "If you ever want a fast, hassle-free sale, we're here. "
        "Take care, and feel free to reach out anytime."
    ),
}

# For touches beyond 3, we cycle through a set of light check-in templates
_CYCLE_TEMPLATES: list[str] = [
    "Hi {first_name}, quick check-in — still a cash buyer interested in {address} "
    "whenever the timing is right for you.",
    "Hi {first_name}, hope all is well. Just staying in touch about {address} "
    "in case you're ready to talk. No pressure.",
    "Hi {first_name}, still here if you ever want a no-hassle cash sale on {address}. "
    "Happy to work around your schedule.",
]


def get_template(status_bucket: str, touch_number: int) -> str:
    """
    Return the raw message body for the given status bucket and touch number.
    Touch numbers > 3 cycle through _CYCLE_TEMPLATES.
    """
    if touch_number <= 3:
        key = (status_bucket.lower(), touch_number)
        if key in _TEMPLATES:
            return _TEMPLATES[key]
        # Fallback to nurture bucket
        key = (NURTURE_BUCKET, min(touch_number, 3))
        return _TEMPLATES.get(key, _CYCLE_TEMPLATES[0])
    # Cycle templates for later touches
    idx = (touch_number - 4) % len(_CYCLE_TEMPLATES)
    return _CYCLE_TEMPLATES[idx]


def render_template(
    status_bucket: str,
    touch_number: int,
    first_name: str | None,
    address: str,
    city: str = "Indianapolis",
) -> str:
    """
    Render the raw SMS body (no disclosure, no opt-out).
    The caller must pass through build_seller_sms() before sending.
    """
    template = get_template(status_bucket, touch_number)
    return template.format(
        first_name=first_name or "there",
        address=address,
        city=city or "Indianapolis",
    )
