"""
Vendor adapter interfaces (§4 principle 8 — no vendor lock-in).

Each adapter module exposes a concrete implementation behind a stable
interface so the underlying vendor can be swapped without touching agent code.
Current adapters: esign (stub until e-sign vendor is chosen).
"""
