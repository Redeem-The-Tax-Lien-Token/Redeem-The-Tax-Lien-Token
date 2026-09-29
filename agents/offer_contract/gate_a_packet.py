"""
Gate A packet assembler for Agent 8.

Builds the structured payload sent to the operator for Gate A approval:
  - Both deal cases (wholesale + BRRRR) from the underwriting snapshot
  - Decision memo from decision_memo.py
  - Contract preview (rendered PSA text)
  - Risk flags

All numbers come from the underwriting snapshot stored in
deals.underwriting_snapshot — never from the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .contract_builder import build_psa
from .decision_memo import write_decision_memo


@dataclass
class GateAPacket:
    lead_id:              int
    deal_id:              int
    seller_name:          str
    property_address:     str
    offer_amount:         float
    strategy:             str          # "wholesale" | "brrrr"
    wholesale_case:       dict | None
    brrrr_case:           dict | None
    decision_memo:        str          # plain-English Claude output
    contract_preview:     str          # rendered PSA text
    disclosure_version:   str
    fallback_fee:         float | None
    fallback_flag:        str | None   # "NO_EXIT_IF_FUNDING_FAILS" | None
    risk_flags:           list[str] = field(default_factory=list)
    generated_at:         datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lead_id":            self.lead_id,
            "deal_id":            self.deal_id,
            "seller_name":        self.seller_name,
            "property_address":   self.property_address,
            "offer_amount":       self.offer_amount,
            "strategy":           self.strategy,
            "wholesale_case":     self.wholesale_case,
            "brrrr_case":         self.brrrr_case,
            "decision_memo":      self.decision_memo,
            "contract_preview":   self.contract_preview,
            "disclosure_version": self.disclosure_version,
            "fallback_fee":       self.fallback_fee,
            "fallback_flag":      self.fallback_flag,
            "risk_flags":         self.risk_flags,
            "generated_at":       self.generated_at.isoformat(),
        }


def _extract_risk_flags(
    strategy: str,
    snapshot: dict,
    fallback_fee: float | None,
    fallback_flag: str | None,
    cfg: dict,
) -> list[str]:
    flags: list[str] = []
    if fallback_flag:
        flags.append(fallback_flag)
    min_fee = cfg.get("wholesale", {}).get("min_wholesale_fee", 7500)
    if fallback_fee is not None and fallback_fee < min_fee:
        flags.append(f"FALLBACK_FEE_BELOW_MINIMUM (${fallback_fee:,.0f} < ${min_fee:,.0f})")
    bc = snapshot.get("brrrr_case") or {}
    if strategy == "brrrr":
        dscr = bc.get("dscr", 0)
        min_dscr = cfg.get("brrrr", {}).get("min_dscr", 1.25)
        if dscr and dscr < min_dscr * 1.05:
            flags.append(f"DSCR_NEAR_FLOOR ({dscr:.2f})")
        cash_left = bc.get("cash_left_in", 0)
        max_left = cfg.get("brrrr", {}).get("max_cash_left_in", 10000)
        if cash_left is not None and cash_left > max_left * 0.80:
            flags.append(f"CASH_LEFT_IN_NEAR_MAX (${cash_left:,.0f})")
    wc = snapshot.get("wholesale_case") or {}
    arv_conf = wc.get("arv_confidence") or bc.get("arv_confidence") or ""
    if arv_conf == "low":
        flags.append("LOW_ARV_CONFIDENCE")
    return flags


def build_gate_a_packet(
    *,
    item: dict[str, Any],
    cfg: dict[str, Any],
    use_llm: bool = True,
) -> GateAPacket:
    """
    Assemble the full Gate A packet for a given queue item.

    Parameters
    ----------
    item     : row from fetch_gate_a_item() / fetch_gate_a_queue()
    cfg      : parsed strategy.yaml
    use_llm  : set False in tests / dry-run to skip the Claude API call

    The decision memo is written by decision_memo.write_decision_memo()
    which calls Claude with the frozen numbers. Claude cannot alter the
    numbers; it only writes the explanation.
    """
    snapshot: dict = item.get("underwriting_snapshot") or {}
    wholesale_case = snapshot.get("wholesale_case")
    brrrr_case     = snapshot.get("brrrr_case")

    offer_amount     = float(item.get("offer_amount") or 0)
    strategy         = item.get("strategy") or "wholesale"
    fallback_fee_raw = item.get("fallback_fee")
    fallback_fee     = float(fallback_fee_raw) if fallback_fee_raw is not None else None
    fallback_flag    = item.get("fallback_flag")

    # Closing date may be a date object or a string from the DB
    closing_date_raw = item.get("closing_date")
    if isinstance(closing_date_raw, str):
        closing_date = date.fromisoformat(closing_date_raw)
    elif isinstance(closing_date_raw, date):
        closing_date = closing_date_raw
    else:
        closing_date = date.today()

    # Render the contract preview — disclosure injected by build_psa(), never LLM
    contract_text, disclosure_version = build_psa(
        seller_name=item.get("seller_name") or "Seller",
        property_address=item.get("address") or "",
        property_city=item.get("city") or "",
        property_state=item.get("state") or "IN",
        property_zip=item.get("zip") or "",
        attom_id=None,
        purchase_price=int(offer_amount),
        emd_amount=int(
            item.get("emd_amount")
            or cfg.get("contract", {}).get("emd_amount", 1000)
        ),
        inspection_period_days=int(
            item.get("inspection_period_days")
            or cfg.get("contract", {}).get("inspection_period_days", {}).get(strategy, 10)
        ),
        closing_date=closing_date,
        cfg=cfg,
    )

    # Decision memo: Claude explains the numbers it was given (§3 Step 5)
    if use_llm:
        memo = write_decision_memo(
            strategy=strategy,
            wholesale_case=wholesale_case,
            brrrr_case=brrrr_case,
            offer_amount=offer_amount,
            fallback_fee=fallback_fee,
            fallback_flag=fallback_flag,
        )
    else:
        memo = "[Decision memo suppressed — use_llm=False]"

    risk_flags = _extract_risk_flags(strategy, snapshot, fallback_fee, fallback_flag, cfg)

    return GateAPacket(
        lead_id=item["lead_id"],
        deal_id=item["deal_id"],
        seller_name=item.get("seller_name") or "Seller",
        property_address=item.get("address") or "",
        offer_amount=offer_amount,
        strategy=strategy,
        wholesale_case=wholesale_case,
        brrrr_case=brrrr_case,
        decision_memo=memo,
        contract_preview=contract_text,
        disclosure_version=disclosure_version,
        fallback_fee=fallback_fee,
        fallback_flag=fallback_flag,
        risk_flags=risk_flags,
    )
