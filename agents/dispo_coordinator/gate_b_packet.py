"""
Gate B packet assembler for Agent 11.

Builds the structured payload sent to the operator for Gate B (wholesale)
approval: selected buyer's qualification, all competing offers, assignment
agreement preview, deal financials.

All numbers come from the DB and underwriting_snapshot — never the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .assignment_builder import build_assignment
from .buyer_qualifier import qualify_buyer, rank_offers, QualificationResult


@dataclass
class GateBPacket:
    deal_id:             int
    lead_id:             int
    property_address:    str
    seller_name:         str
    psa_price:           float
    strategy:            str
    selected_buyer:      dict | None
    qualified_offers:    list[dict]
    all_offers:          list[dict]
    assignment_preview:  str
    disclosure_version:  str
    assignment_fee:      float
    risk_flags:          list[str] = field(default_factory=list)
    generated_at:        datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "deal_id":            self.deal_id,
            "lead_id":            self.lead_id,
            "property_address":   self.property_address,
            "seller_name":        self.seller_name,
            "psa_price":          self.psa_price,
            "strategy":           self.strategy,
            "selected_buyer":     self.selected_buyer,
            "qualified_offers":   self.qualified_offers,
            "all_offers":         self.all_offers,
            "assignment_preview": self.assignment_preview,
            "disclosure_version": self.disclosure_version,
            "assignment_fee":     self.assignment_fee,
            "risk_flags":         self.risk_flags,
            "generated_at":       self.generated_at.isoformat(),
        }


def build_gate_b_packet(
    *,
    item: dict[str, Any],
    all_offers: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> GateBPacket:
    """
    Assemble the Gate B packet.

    Parameters
    ----------
    item        : row from fetch_gate_b_item()
    all_offers  : rows from fetch_buyer_offers() for this deal
    cfg         : parsed strategy.yaml
    """
    deal_id   = item["deal_id"]
    lead_id   = item["lead_id"]
    psa_price = float(item.get("psa_price") or 0)

    deal_for_qual = {
        "emd_amount":   item.get("emd_amount"),
        "offer_amount": psa_price,
    }

    # Re-qualify all offers (authoritative re-check at Gate B time)
    qualified = []
    for offer in all_offers:
        result: QualificationResult = qualify_buyer(offer, deal_for_qual, cfg)
        enriched = {**offer, "qualification": result.__dict__, "qualified": result.qualified}
        if result.qualified:
            qualified.append(enriched)

    ranked_qualified = rank_offers(qualified, deal_for_qual)

    # Selected buyer is the one already stored on the deal
    selected_buyer_id = item.get("selected_buyer_id") or item.get("buyer_id")
    selected_buyer = None
    if selected_buyer_id:
        for o in ranked_qualified:
            if o.get("buyer_id") == selected_buyer_id:
                selected_buyer = o
                break

    # Assignment fee = buyer_offer_amount - psa_price (the spread)
    assignment_fee_raw = item.get("assignment_fee_actual")
    if assignment_fee_raw:
        assignment_fee = float(assignment_fee_raw)
    elif selected_buyer:
        assignment_fee = float(selected_buyer.get("buyer_offer_amount") or 0) - psa_price
    else:
        assignment_fee = 0.0

    # Risk flags
    risk_flags: list[str] = []
    min_fee = float(cfg.get("wholesale", {}).get("min_wholesale_fee", 7500))
    if assignment_fee < min_fee:
        risk_flags.append(f"FEE_BELOW_MINIMUM (${assignment_fee:,.0f} < ${min_fee:,.0f})")
    if len(qualified) == 0:
        risk_flags.append("NO_QUALIFIED_BUYERS")
    elif len(qualified) == 1:
        risk_flags.append("ONLY_ONE_QUALIFIED_BUYER")
    closing_date_raw = item.get("closing_date")
    if closing_date_raw:
        if isinstance(closing_date_raw, str):
            closing_date_obj = date.fromisoformat(closing_date_raw)
        else:
            closing_date_obj = closing_date_raw
        days_to_close = (closing_date_obj - date.today()).days
        if days_to_close < 14:
            risk_flags.append(f"CLOSING_IMMINENT ({days_to_close}d remaining)")

    # Build assignment agreement preview
    psa_date_raw = item.get("deal_updated_at") or datetime.utcnow()
    if isinstance(psa_date_raw, datetime):
        psa_date = psa_date_raw.date()
    else:
        psa_date = date.today()

    closing_date = date.today()
    if closing_date_raw:
        if isinstance(closing_date_raw, str):
            closing_date = date.fromisoformat(closing_date_raw)
        else:
            closing_date = closing_date_raw

    entity_name = cfg.get("contract", {}).get("entity_name", "Redeem Real Estate LLC and/or assigns")
    emd_for_assignment = max(500, int(assignment_fee * 0.10))  # 10% of fee, min $500

    assignment_text, disclosure_version = build_assignment(
        assignor_name=entity_name,
        assignee_name=(selected_buyer or {}).get("buyer_name") or "Buyer TBD",
        seller_name=item.get("seller_name") or "Seller",
        property_address=item.get("address") or "",
        property_city=item.get("city") or "",
        property_state=item.get("state") or "IN",
        property_zip=item.get("zip") or "",
        psa_price=int(psa_price),
        psa_date=psa_date,
        assignment_fee=int(assignment_fee),
        assignment_emd=emd_for_assignment,
        closing_date=closing_date,
        cfg=cfg,
    )

    return GateBPacket(
        deal_id=deal_id,
        lead_id=lead_id,
        property_address=item.get("address") or "",
        seller_name=item.get("seller_name") or "Seller",
        psa_price=psa_price,
        strategy=item.get("strategy") or "wholesale",
        selected_buyer=selected_buyer,
        qualified_offers=ranked_qualified,
        all_offers=[{**o, "qualified": qualify_buyer(o, deal_for_qual, cfg).qualified}
                    for o in all_offers],
        assignment_preview=assignment_text,
        disclosure_version=disclosure_version,
        assignment_fee=assignment_fee,
        risk_flags=risk_flags,
    )
