"""
Sector-aware weight redistribution (section 20).

Some factors are conceptually meaningless for certain sectors — e.g. TVL
doesn't apply to an L1 base asset or a memecoin the way it does to a
DeFi lending protocol (section 20's own explicit example: "A DePIN
project should not be judged primarily by TVL"). Rather than let those
factors sit at insufficient_data forever — which implies "we just don't
have the data yet" — this module lets a scoring model declare "sector X
doesn't have this factor at all" and permanently zero-weight it FOR THAT
SECTOR, redistributing the freed weight proportionally onto the sector's
remaining, meaningful factors.

This is a genuinely different kind of exclusion from insufficient_data:
insufficient_data means "this COULD apply, we just don't have the data
yet — more data collection might fix it." A sector-zeroed factor means
"this fundamentally doesn't apply to this kind of asset — no amount of
data collection changes that." Both end up excluded from the weighted
average the same mechanical way (weight=0, insufficient_data=True on the
Factor, so core.scoring.base's renormalization treats them identically),
but the note text distinguishes which kind of exclusion it is, since the
two have very different implications for whether the gap will ever close.

Scope, deliberately narrow: only implements what section 20 gives an
explicit example for (TVL isn't meaningful for every sector). A fuller
per-sector metric taxonomy across all 4 scores and all 18 sectors would
require judgment calls this codebase has no spec'd basis for yet — see
PHASE_7_NOTES.md's "Known issues" for what's NOT covered here.
"""

from decimal import Decimal
from typing import Optional

from core.models import Asset

# Sectors where "value locked in the protocol" is a natural, meaningful
# concept — capital deposited that the protocol's core economics revolve
# around. Everything else doesn't have a natural TVL metric, per section
# 20's own DePIN example. Judgment call, not exhaustive: some assets
# outside this set may still incidentally have a DefiLlama-tracked TVL
# figure (e.g. a bridge), but TVL isn't the PRIMARY lens for judging them.
TVL_RELEVANT_SECTORS = {
    Asset.Sector.DEFI,
    Asset.Sector.DEX,
    Asset.Sector.LENDING,
    Asset.Sector.DERIVATIVES,
    Asset.Sector.STABLECOIN_INFRA,
}


def is_tvl_relevant(sector: Optional[str]) -> bool:
    """Unclassified assets (sector=None) default to True — meaning
    "don't zero out TVL factors" — since we have no basis to say TVL
    doesn't apply without a successful sector classification. Sector-
    based exclusion should never apply to an asset we haven't classified."""
    if sector is None:
        return True
    return sector in TVL_RELEVANT_SECTORS


def redistribute_weights(base_weights: dict[str, Decimal], zero_out: set[str]) -> dict[str, Decimal]:
    """Zero out the given factor names and redistribute their combined
    weight proportionally across the remaining factors, preserving the
    total sum EXACTLY (the last remaining factor absorbs any rounding
    remainder rather than letting proportional Decimal division drift the
    total away from the original sum). Names not present in base_weights
    are ignored silently (defensive — a typo in zero_out shouldn't
    corrupt the weight budget)."""
    zero_out = {n for n in zero_out if n in base_weights}
    zeroed_total = sum((base_weights[n] for n in zero_out), Decimal("0"))
    remaining_names = [n for n in base_weights if n not in zero_out]
    remaining_total = sum((base_weights[n] for n in remaining_names), Decimal("0"))

    if zeroed_total <= 0 or remaining_total <= 0:
        return dict(base_weights)

    result: dict[str, Decimal] = {n: Decimal("0") for n in zero_out}
    running_total = Decimal("0")
    for n in remaining_names[:-1]:
        w = base_weights[n]
        adjusted = w + (w / remaining_total) * zeroed_total
        result[n] = adjusted
        running_total += adjusted

    # Last factor gets whatever makes the total exact, rather than its
    # own proportional share — avoids Decimal division leaving the sum a
    # few units of epsilon away from the original total.
    target_total = sum(base_weights.values())
    result[remaining_names[-1]] = target_total - running_total

    return result
