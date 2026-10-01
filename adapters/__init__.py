"""
Vendor adapter interfaces (§4 principle 8 — no vendor lock-in).

Each adapter module exposes a concrete implementation behind a stable
interface so the underlying vendor can be swapped without touching agent code.

Adapters:
    esign              — e-sign (DocuSign / PandaDoc / HelloSign stub)
    lender             — acquisition and refi lender portal stub
    listing_syndication— rental listing syndication (Zillow, etc.) stub
    tenant_screening   — Fair-Housing-compliant applicant screening stub
    rent_comps         — rental comparable lookup; manual-entry stub (phase 1)
    payments           — EMD, draws, and deal payments stub (Gate B required)
    accounting_export  — QuickBooks / Xero / Wave transaction export stub

All adapters respect SYSTEM_MODE=dry_run: they log the intent and return a
stub result without making any external call or moving any money.  In live
mode they raise NotImplementedError until the real vendor is wired in.
"""
