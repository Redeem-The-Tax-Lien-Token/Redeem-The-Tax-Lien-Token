"""
Deterministic repair-cost estimator for the ARV/MAO agent.

Decision chain (highest to lowest priority):
  1. vision condition (medium/high confidence)
  2. ATTOM condition field
  3. year-built age default
  4. fallback: 'medium' tier when everything is unknown

Distress floor: tax_delinquent / pre_foreclosure / vacant floors the
resolved tier at 'medium' regardless of what age/vision computed (those
motivation types correlate with deferred maintenance even on newer houses).

Repair estimate = max(arv_mid * tier_arv_pct,  tier_sqft_floor * sqft)
When sqft is unknown the arv_pct branch is used alone.
"""

from __future__ import annotations

# ── Tier table ────────────────────────────────────────────────────────────────
# arv_pct  : fraction of arv_mid used as the % floor
# sqft_rate: $/sqft hard floor
_TIERS: dict[str, dict] = {
    "good":       {"arv_pct": 0.05, "sqft_rate": 15.0},
    "fair":       {"arv_pct": 0.10, "sqft_rate": 25.0},
    "medium":     {"arv_pct": 0.15, "sqft_rate": 35.0},
    "poor":       {"arv_pct": 0.20, "sqft_rate": 50.0},
    "distressed": {"arv_pct": 0.25, "sqft_rate": 65.0},
}

_TIER_ORDER = ["good", "fair", "medium", "poor", "distressed"]
_DISTRESS_MOTIVATIONS = {"tax_delinquent", "pre_foreclosure", "vacant"}

# ── ATTOM condition → tier ────────────────────────────────────────────────────
# ATTOM uses text labels; sometimes numeric C1-C6 scale.
_ATTOM_MAP: dict[str, str] = {
    # text labels
    "excellent":  "good",
    "very good":  "good",
    "good":       "fair",
    "average":    "medium",
    "fair":       "poor",
    "poor":       "distressed",
    "very poor":  "distressed",
    # C-scale (C1=best, C6=worst)
    "c1": "good",
    "c2": "fair",
    "c3": "medium",
    "c4": "poor",
    "c5": "distressed",
    "c6": "distressed",
}

# ── Age-based defaults ────────────────────────────────────────────────────────
def _tier_from_age(year_built: int | None) -> str:
    if year_built is None:
        return "medium"
    import datetime
    age = datetime.date.today().year - year_built
    if age < 10:
        return "good"
    if age < 25:
        return "fair"
    if age < 50:
        return "medium"
    return "poor"


def _parse_attom_condition(raw: str | None) -> str | None:
    if not raw:
        return None
    return _ATTOM_MAP.get(raw.strip().lower())


def _apply_distress_floor(tier: str, motivation_type: str | None) -> tuple[str, bool]:
    """Return (floored_tier, was_floored)."""
    if motivation_type and motivation_type.lower() in _DISTRESS_MOTIVATIONS:
        idx = _TIER_ORDER.index(tier)
        floor_idx = _TIER_ORDER.index("medium")
        if idx < floor_idx:
            return "medium", True
    return tier, False


def estimate(
    arv_mid: float,
    sqft: float | None,
    year_built: int | None,
    attom_condition: str | None,
    vision_condition: str | None,
    vision_confidence: str | None,
    motivation_type: str | None,
) -> dict:
    """
    Return:
      repair_estimate : float  (dollars)
      repair_tier     : str    one of _TIERS keys
      repair_basis    : str    human-readable explanation of which signal drove it
    """
    basis_parts: list[str] = []
    tier: str | None = None

    # 1. Vision (medium or high confidence only)
    if vision_condition and vision_confidence in ("medium", "high"):
        vision_tier = _ATTOM_MAP.get(vision_condition.strip().lower())
        if vision_tier:
            tier = vision_tier
            basis_parts.append(f"vision ({vision_condition}, {vision_confidence} confidence)")

    # 2. ATTOM condition field
    if tier is None:
        attom_tier = _parse_attom_condition(attom_condition)
        if attom_tier:
            tier = attom_tier
            basis_parts.append(f"ATTOM condition ({attom_condition})")

    # 3. Age-only default
    if tier is None:
        tier = _tier_from_age(year_built)
        if year_built:
            import datetime
            age = datetime.date.today().year - year_built
            basis_parts.append(f"age ({year_built}, {age}yrs old)")
        else:
            basis_parts.append("age unknown — default medium")

    # 4. Distress motivation floor
    tier, was_floored = _apply_distress_floor(tier, motivation_type)
    if was_floored:
        basis_parts.append(f"distress floor applied ({motivation_type})")

    # Compute dollar amount
    t = _TIERS[tier]
    arv_branch  = arv_mid * t["arv_pct"]
    sqft_branch = (sqft * t["sqft_rate"]) if sqft else 0.0
    repair_estimate = max(arv_branch, sqft_branch)

    return {
        "repair_estimate": round(repair_estimate, 2),
        "repair_tier":     tier,
        "repair_basis":    "; ".join(basis_parts),
    }
