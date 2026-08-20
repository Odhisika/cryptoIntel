"""
10X mathematics (section 13).

This module answers only "what would a 10X require, mathematically" — it
makes no claim about whether that's plausible. Plausibility is a scoring
question (core.scoring.potential_10x), not a math question. Keeping these
separate is the point: the spec is explicit that "10X Mathematical
Possibility" and "10X Plausibility" are not the same thing.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class TenXMath:
    current_market_cap_usd: Decimal
    target_multiple: Decimal
    target_market_cap_usd: Decimal
    required_market_cap_growth_pct: Decimal  # e.g. 900 means +900%
    current_price_usd: Decimal
    required_token_price_usd: Decimal
    current_fdv_usd: Optional[Decimal]
    target_fdv_usd: Optional[Decimal]
    fdv_market_cap_gap_pct: Optional[Decimal]  # how much of target MC is "pre-diluted away" by FDV


def compute_10x_math(
    *,
    current_market_cap_usd: Decimal,
    current_price_usd: Decimal,
    current_fdv_usd: Optional[Decimal] = None,
    target_multiple: Decimal = Decimal("10"),
) -> TenXMath:
    """Pure math, no data lookups, no scoring judgment.

    Raises ValueError on non-positive inputs — a $0 or negative market cap
    is a data-quality problem upstream, not something this function should
    silently paper over with a fabricated ratio.
    """

    if current_market_cap_usd <= 0:
        raise ValueError("current_market_cap_usd must be positive")
    if current_price_usd <= 0:
        raise ValueError("current_price_usd must be positive")
    if target_multiple <= 0:
        raise ValueError("target_multiple must be positive")

    target_market_cap_usd = current_market_cap_usd * target_multiple
    required_growth_pct = (target_multiple - Decimal("1")) * Decimal("100")
    required_token_price_usd = current_price_usd * target_multiple

    target_fdv_usd = None
    fdv_gap_pct = None
    if current_fdv_usd is not None and current_fdv_usd > 0:
        # If price grows N-fold, FDV grows N-fold too (assuming max supply
        # is fixed — it usually is, modulo emissions/burns handled
        # elsewhere in the tokenomics engine, Phase 4).
        target_fdv_usd = current_fdv_usd * target_multiple
        if target_fdv_usd > 0:
            # How much bigger the fully-diluted target is than the
            # circulating-only target — a proxy for how much dilution
            # overhang eats into the "real" target, before the tokenomics
            # engine (Phase 4) can give a precise unlock-adjusted number.
            fdv_gap_pct = ((target_fdv_usd - target_market_cap_usd) / target_market_cap_usd) * Decimal("100")

    return TenXMath(
        current_market_cap_usd=current_market_cap_usd,
        target_multiple=target_multiple,
        target_market_cap_usd=target_market_cap_usd,
        required_market_cap_growth_pct=required_growth_pct,
        current_price_usd=current_price_usd,
        required_token_price_usd=required_token_price_usd,
        current_fdv_usd=current_fdv_usd,
        target_fdv_usd=target_fdv_usd,
        fdv_market_cap_gap_pct=fdv_gap_pct,
    )
