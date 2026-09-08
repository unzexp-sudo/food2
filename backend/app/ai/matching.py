"""SKU & unit matching for intake line normalization.

Resolution order (per §5 of the intake contract):
  1. alias_exact   — customer_product_aliases.alias == raw name (case-insensitive, trimmed)
  2. catalog_exact — Product.name_zh == name OR Product.name_en == name (case-insensitive)
  3. fuzzy         — difflib.get_close_ratio ≥ 0.85 against name_zh/name_en/aliases
  4. unmatched     — product_id None, confidence low

Confidence: 1.0 alias_exact, 0.95 catalog_exact, 0.8 fuzzy, 0.3 unmatched.

Unit resolution: exact code match (jin/kg/box/bag/piece), else name_zh/name_en
match, else leave null (defaults to the product's default unit at the caller level).
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models import CustomerProductAlias, Product, Unit


@dataclass
class ProductMatch:
    product_id: str | None
    product_name: str | None  # display name (matched product name_zh or raw)
    confidence: float
    match_method: str  # alias_exact|catalog_exact|fuzzy|unmatched


@dataclass
class UnitMatch:
    unit_id: str | None
    unit_code: str | None


# Unit name synonyms to code, for robust matching of common variants.
_UNIT_SYNONYMS: dict[str, str] = {
    # jin
    "斤": "jin",
    "jin": "jin",
    "市斤": "jin",
    # kg
    "公斤": "kg",
    "kg": "kg",
    "千克": "kg",
    "kilo": "kg",
    "kilogram": "kg",
    # box
    "箱": "box",
    "box": "box",
    "boxes": "box",
    # bag
    "袋": "bag",
    "包": "bag",
    "bag": "bag",
    "bags": "bag",
    # piece
    "个": "piece",
    "份": "piece",
    "piece": "piece",
    "pcs": "piece",
    "pieces": "piece",
    "只": "piece",
}


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def match_product(
    db: Session,
    *,
    raw_name: str,
    customer_id: str | None,
    products: list[Product],
    aliases: list[CustomerProductAlias],
    fuzzy_threshold: float = 0.85,
) -> ProductMatch:
    """Resolve a raw product name to a catalog product.

    `products` and `aliases` should be pre-loaded for the relevant scope
    (catalog + customer aliases) so we don't N+1 inside the line loop.
    """
    name = (raw_name or "").strip()
    if not name:
        return ProductMatch(None, None, 0.3, "unmatched")

    key = _norm(name)

    # 1. alias_exact — customer aliases (case-insensitive, trimmed)
    if customer_id:
        for a in aliases:
            if a.customer_id == customer_id and _norm(a.alias) == key:
                prod = next((p for p in products if p.id == a.product_id), None)
                if prod:
                    return ProductMatch(
                        prod.id,
                        prod.name_zh or prod.name_en,
                        1.0,
                        "alias_exact",
                    )

    # 2. catalog_exact — name_zh or name_en
    for p in products:
        if _norm(p.name_zh) == key or _norm(p.name_en) == key:
            return ProductMatch(
                p.id,
                p.name_zh or p.name_en,
                0.95,
                "catalog_exact",
            )

    # 3. fuzzy — difflib against name_zh, name_en, and any alias text
    candidates: list[tuple[float, Product]] = []
    name_zh_list = [(p, p.name_zh) for p in products]
    name_en_list = [(p, p.name_en) for p in products]
    alias_pairs = [
        (next((p for p in products if p.id == a.product_id), None), a.alias)
        for a in aliases
        if customer_id is None or a.customer_id == customer_id
    ]

    for prod, candidate_name in (
        name_zh_list + name_en_list + [(p, a_name) for p, a_name in alias_pairs if p is not None]
    ):
        ratio = difflib.SequenceMatcher(None, key, _norm(candidate_name)).ratio()
        if ratio >= fuzzy_threshold:
            candidates.append((ratio, prod))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_ratio, best_prod = candidates[0]
        # Cap fuzzy confidence at 0.8 per contract
        conf = min(best_ratio, 0.8)
        return ProductMatch(
            best_prod.id,
            best_prod.name_zh or best_prod.name_en,
            conf,
            "fuzzy",
        )

    # 4. unmatched
    return ProductMatch(None, raw_name, 0.3, "unmatched")


def match_unit(
    db: Session,
    *,
    raw_unit: str | None,
    units: list[Unit],
    fallback_product: Product | None = None,
) -> UnitMatch:
    """Resolve a raw unit string to a Unit row.

    Order: exact code match → synonym → name_zh/name_en match → product default.
    Returns UnitMatch(None, None) if nothing resolves.
    """
    raw = (raw_unit or "").strip()
    if raw:
        key = _norm(raw)

        # exact code match
        for u in units:
            if _norm(u.code) == key:
                return UnitMatch(u.id, u.code)

        # synonym map
        code = _UNIT_SYNONYMS.get(key) or _UNIT_SYNONYMS.get(raw)
        if code:
            for u in units:
                if u.code == code:
                    return UnitMatch(u.id, u.code)

        # name_zh / name_en match
        for u in units:
            if _norm(u.name_zh) == key or _norm(u.name_en) == key:
                return UnitMatch(u.id, u.code)

    # fallback to product default unit
    if fallback_product and fallback_product.default_unit_id:
        for u in units:
            if u.id == fallback_product.default_unit_id:
                return UnitMatch(u.id, u.code)

    return UnitMatch(None, None)
