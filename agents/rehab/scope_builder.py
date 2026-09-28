"""
Scope-of-work builder for Agent 13.

Turns the underwriting repair estimate (a tier + total) into a structured
line-item scope.  The LLM provides initial line-item suggestions (if
use_llm=True and ANTHROPIC_API_KEY is set); the operator approves the final
scope at Gate C.  All budget math is deterministic Python.

Public API:
    STANDARD_CATEGORIES        — ordered category list
    build_scope_from_estimate(repair_estimate, tier, cfg=None) -> list[ScopeItem]
    total_scope_budget(items) -> float
    validate_scope(items) -> list[str]   — returns error strings
    ScopeItem                  — dataclass
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

TierLiteral = Literal["LIGHT", "MEDIUM", "HEAVY", "GUT"]

STANDARD_CATEGORIES: list[str] = [
    "demo_cleanup",
    "foundation_structure",
    "roof",
    "exterior",
    "plumbing",
    "electrical",
    "hvac",
    "insulation",
    "drywall_plaster",
    "flooring",
    "kitchen",
    "bathrooms",
    "doors_windows",
    "paint_interior",
    "paint_exterior",
    "landscaping_curb",
    "permits_inspections",
    "contingency",
    "other",
]

# Rough category weight by rehab tier (shares of total estimate)
# Only used when building a default scope from a lump-sum estimate.
_TIER_WEIGHTS: dict[TierLiteral, dict[str, float]] = {
    "LIGHT": {
        "flooring": 0.20,
        "paint_interior": 0.20,
        "kitchen": 0.15,
        "bathrooms": 0.15,
        "doors_windows": 0.10,
        "landscaping_curb": 0.05,
        "permits_inspections": 0.05,
        "contingency": 0.10,
    },
    "MEDIUM": {
        "roofing": 0.10,
        "plumbing": 0.10,
        "electrical": 0.08,
        "hvac": 0.08,
        "flooring": 0.12,
        "paint_interior": 0.10,
        "kitchen": 0.12,
        "bathrooms": 0.10,
        "doors_windows": 0.05,
        "landscaping_curb": 0.05,
        "permits_inspections": 0.05,
        "contingency": 0.05,
    },
    "HEAVY": {
        "foundation_structure": 0.05,
        "roof": 0.10,
        "exterior": 0.07,
        "plumbing": 0.10,
        "electrical": 0.10,
        "hvac": 0.08,
        "drywall_plaster": 0.07,
        "flooring": 0.08,
        "kitchen": 0.08,
        "bathrooms": 0.07,
        "doors_windows": 0.05,
        "paint_interior": 0.05,
        "permits_inspections": 0.05,
        "contingency": 0.05,
    },
    "GUT": {
        "demo_cleanup": 0.06,
        "foundation_structure": 0.05,
        "roof": 0.08,
        "exterior": 0.07,
        "plumbing": 0.10,
        "electrical": 0.10,
        "hvac": 0.08,
        "insulation": 0.04,
        "drywall_plaster": 0.07,
        "flooring": 0.07,
        "kitchen": 0.07,
        "bathrooms": 0.06,
        "doors_windows": 0.04,
        "paint_interior": 0.04,
        "permits_inspections": 0.04,
        "contingency": 0.03,
    },
}


@dataclass
class ScopeItem:
    category:    str
    description: str
    total_cost:  float
    quantity:    float | None = None
    unit:        str | None   = None
    unit_cost:   float | None = None
    sort_order:  int = 0

    def to_dict(self) -> dict:
        return {
            "category":    self.category,
            "description": self.description,
            "total_cost":  self.total_cost,
            "quantity":    self.quantity,
            "unit":        self.unit,
            "unit_cost":   self.unit_cost,
            "sort_order":  self.sort_order,
        }


def build_scope_from_estimate(
    repair_estimate: float,
    tier: TierLiteral,
    cfg: dict | None = None,
) -> list[ScopeItem]:
    """
    Build a default scope from a lump-sum repair estimate and rehab tier.

    This produces a starting point for the operator / contractor to refine.
    Each category's budget is the estimate × that tier's weight.
    Categories with 0 weight are omitted.
    """
    weights = _TIER_WEIGHTS.get(tier, _TIER_WEIGHTS["MEDIUM"])
    items: list[ScopeItem] = []

    for i, (cat, weight) in enumerate(weights.items()):
        amt = round(repair_estimate * weight, 2)
        if amt <= 0:
            continue
        items.append(ScopeItem(
            category=cat,
            description=_default_description(cat, tier),
            total_cost=amt,
            sort_order=i,
        ))

    return items


def _default_description(category: str, tier: TierLiteral) -> str:
    prefix = {"LIGHT": "Light", "MEDIUM": "Standard", "HEAVY": "Full", "GUT": "Complete gut"}
    return f"{prefix.get(tier, 'Standard')} {category.replace('_', ' ')}"


def total_scope_budget(items: list[ScopeItem]) -> float:
    return round(sum(i.total_cost for i in items), 2)


def validate_scope(items: list[ScopeItem]) -> list[str]:
    """Return a list of validation error strings.  Empty → scope is valid."""
    errors: list[str] = []

    if not items:
        errors.append("Scope has no line items")
        return errors

    for idx, item in enumerate(items):
        if not item.category:
            errors.append(f"Item {idx}: missing category")
        if not item.description:
            errors.append(f"Item {idx}: missing description")
        if item.total_cost < 0:
            errors.append(f"Item {idx} ({item.category}): total_cost cannot be negative")

    total = total_scope_budget(items)
    if total <= 0:
        errors.append("Total scope budget must be > 0")

    return errors
