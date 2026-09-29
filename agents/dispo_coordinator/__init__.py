"""
Agent 11 — Dispo & Closing Coordinator.

Wholesale track from buyer selection through fee receipt:
  buyer_selected → [Gate B] → assigned → title_open_w → clear_to_close_w
  → closed_w → fee_received

Never performs BRRRR closing — those are handled by Agent 12 (Acquisition)
and Agent 15 (Refinance).  Gate B for BRRRR is out of scope here.
"""
