"""
Undervaluation Score, model v1.3 (sector-aware weighting added in Phase 7).

Per section 15, this score compares protocol economics (revenue, fees,
TVL, users) to valuation. As of 3.1b: TVL (3.1) and fees/revenue (3.1b)
are real; users still need a separate data source (not yet identified —
DefiLlama doesn't publish user counts on the free tier).

Revenue and fees are ANNUALIZED from DefiLlama's 30-day rolling total
(x12) for the fees ratio, and from the 24h figure (x365) for revenue —
DefiLlama's /summary/fees endpoint gives a 30D rolling fee total but only
a single daily revenue figure. A 30D window smooths single-day spikes
better than a 24h annualization would, at the cost of reacting slower to
genuine step-changes. This is a judgment call, not a neutral default;
flagged for reconsideration once Phase 10 backtesting exists to check
which window actually correlates with anything.

SECTOR-AWARE WEIGHTING (Phase 7, section 20): for assets outside
core.scoring.sector_weights.TVL_RELEVANT_SECTORS (DeFi/DEX/Lending/
Derivatives/Stablecoin Infrastructure), `tvl_to_market_cap` AND
`fundamentals_growth` (which is currently entirely TVL-growth-derived —
see below) are zero-weighted and their combined weight is redistributed
proportionally across the remaining factors. An L1 base asset or a
memecoin no longer has its Undervaluation score structurally capped by a
TVL metric that was never meaningful for it in the first place.
Unclassified assets (sector=None) are treated as TVL-relevant by default
— see is_tvl_relevant()'s docstring for why.

Weight rationale (base weights, before any sector adjustment):
- Revenue/Fees ratios (30 combined): the most direct signal of whether
  users are actually paying for the protocol, which is closer to "hard"
  evidence than TVL (which can be mercenary/incentivized capital). NOW
  COMPUTED for protocols where DefiLlama reports a revenue/fee figure.
- TVL ratios (20): still meaningful but weighted below revenue/fees since
  TVL can inflate without matching usage. Computed since Phase 3.1;
  sector-zeroed for non-TVL-relevant sectors since Phase 7.
- User activity ratios (20): growth in genuine usage, independent of
  price — still insufficient_data, no user-count data source identified.
- Growth rates for the above (20 combined): trend matters as much as
  level. Still TVL-growth-only (see note on the fundamentals_growth
  factor) — fee/revenue growth is a natural extension but not added yet,
  to keep this chunk reviewable as one thing. Sector-zeroed alongside TVL
  for the same reason: a TVL-derived growth number is exactly as
  inapplicable to a non-TVL sector as TVL itself.
- FDV/Revenue, MC/Revenue (10 combined): valuation multiples, analogous
  to P/E-style ratios in traditional finance. NOW COMPUTED where a
  RevenueSnapshot exists (i.e. the protocol takes a fee cut at all — many
  don't, and for those these two factors stay insufficient_data, which is
  the economically correct answer, not a data gap).

A protocol with a FeeSnapshot but NO RevenueSnapshot (fees exist, but the
protocol takes no cut) will have fees_to_market_cap computed but
revenue_to_market_cap / fdv_to_revenue / market_cap_to_revenue stay
insufficient_data — that's a real distinction (see RevenueSnapshot's own
docstring), not a data-quality problem to paper over.
"""

from decimal import Decimal
from typing import Optional

from core.models import Asset, MarketSnapshot, Protocol, RevenueSnapshot
from core.scoring.base import Factor, ScoreResult, compute_weighted_score
from core.scoring.sector_weights import is_tvl_relevant, redistribute_weights

MODEL_NAME = "undervaluation"
MODEL_VERSION = "v1.3"

WEIGHTS = {
    "revenue_to_market_cap": Decimal("15"),
    "fees_to_market_cap": Decimal("15"),
    "tvl_to_market_cap": Decimal("20"),
    "user_activity_to_market_cap": Decimal("20"),
    "fundamentals_growth": Decimal("20"),
    "fdv_to_revenue": Decimal("5"),
    "market_cap_to_revenue": Decimal("5"),
}

_NOT_YET_IMPLEMENTED_NOTE = "No user-activity data source identified yet."
_NOT_APPLICABLE_SECTOR_NOTE = (
    "Not applicable for this asset's sector — TVL isn't a meaningful metric for this asset type; "
    "weight redistributed to revenue/fees/valuation factors."
)

HEALTHY_TVL_TO_MC_RATIO = Decimal("2.0")
HEALTHY_REVENUE_TO_MC_RATIO = Decimal("0.10")  # 10% annualized revenue yield
HEALTHY_FEES_TO_MC_RATIO = Decimal("0.30")  # fees are gross, before LP payout, so a higher bar

# Valuation-multiple scoring thresholds for the revenue-multiple factors:
# 5x or below annualized revenue -> 100 (cheap); 50x or above -> 0
# (expensive), linear between. Placeholder thresholds, same caveat as
# every other threshold in this file.
_MULTIPLE_CHEAP = Decimal("5")
_MULTIPLE_EXPENSIVE = Decimal("50")


def _effective_weights(sector: Optional[str]) -> dict[str, Decimal]:
    if is_tvl_relevant(sector):
        return WEIGHTS
    return redistribute_weights(WEIGHTS, {"tvl_to_market_cap", "fundamentals_growth"})


def _latest_tvl_snapshot(asset: Asset):
    protocol = Protocol.objects.filter(asset=asset, is_active=True).first()
    if protocol is None:
        return None
    return protocol.tvl_snapshots.order_by("-observed_at").first()


def _latest_fee_snapshot(asset: Asset):
    protocol = Protocol.objects.filter(asset=asset, is_active=True).first()
    if protocol is None:
        return None
    return protocol.fee_snapshots.order_by("-observed_at").first()


def _latest_revenue_snapshot(asset: Asset) -> Optional[RevenueSnapshot]:
    protocol = Protocol.objects.filter(asset=asset, is_active=True).first()
    if protocol is None:
        return None
    return protocol.revenue_snapshots.order_by("-observed_at").first()


def _score_ratio(numerator_annualized: Decimal, market_cap_usd: Decimal, healthy_ratio: Decimal) -> Decimal:
    if market_cap_usd <= 0:
        return Decimal("0")
    ratio = numerator_annualized / market_cap_usd
    return min(ratio / healthy_ratio, Decimal("1")) * Decimal("100")


def _score_multiple(multiple: Decimal) -> Decimal:
    if multiple <= _MULTIPLE_CHEAP:
        return Decimal("100")
    if multiple >= _MULTIPLE_EXPENSIVE:
        return Decimal("0")
    span = _MULTIPLE_EXPENSIVE - _MULTIPLE_CHEAP
    return Decimal("100") - ((multiple - _MULTIPLE_CHEAP) / span) * Decimal("100")


def _score_tvl_growth(change_7d_pct: Optional[Decimal]) -> Optional[Decimal]:
    if change_7d_pct is None:
        return None
    if change_7d_pct >= Decimal("20"):
        return Decimal("100")
    if change_7d_pct <= Decimal("-20"):
        return Decimal("0")
    return Decimal("50") + (change_7d_pct / Decimal("20")) * Decimal("50")


def _tvl_factor(tvl_snapshot, market_cap: Decimal, weight: Decimal, sector_relevant: bool) -> Factor:
    if not sector_relevant:
        return Factor(
            name="tvl_to_market_cap", weight=weight,
            normalized_value=None, raw_value=None, insufficient_data=True,
            note=_NOT_APPLICABLE_SECTOR_NOTE,
        )
    if tvl_snapshot is None or market_cap <= 0:
        return Factor(
            name="tvl_to_market_cap", weight=weight,
            normalized_value=None, raw_value=None, insufficient_data=True,
            note="No matched DeFi protocol with TVL data for this asset.",
        )
    return Factor(
        name="tvl_to_market_cap",
        weight=weight,
        normalized_value=_score_ratio(tvl_snapshot.tvl_usd, market_cap, HEALTHY_TVL_TO_MC_RATIO),
        raw_value=f"TVL ${tvl_snapshot.tvl_usd:,.0f} vs MC ${market_cap:,.0f}",
        note="Higher TVL relative to market cap is a candidate signal, not proof, of undervaluation.",
    )


def _fees_factor(fee_snapshot, market_cap: Decimal, weight: Decimal) -> Factor:
    if fee_snapshot is None or fee_snapshot.fees_30d_usd is None or market_cap <= 0:
        return Factor(
            name="fees_to_market_cap", weight=weight,
            normalized_value=None, raw_value=None, insufficient_data=True,
            note="No fee data available for this asset's protocol.",
        )
    annualized_fees = fee_snapshot.fees_30d_usd * Decimal("12")
    return Factor(
        name="fees_to_market_cap",
        weight=weight,
        normalized_value=_score_ratio(annualized_fees, market_cap, HEALTHY_FEES_TO_MC_RATIO),
        raw_value=f"~${annualized_fees:,.0f}/yr fees (from 30D total) vs MC ${market_cap:,.0f}",
        note="Annualized from DefiLlama's 30D rolling fee total (x12).",
    )


def _revenue_factors(
    revenue_snapshot, market_cap: Decimal, fdv: Optional[Decimal], weights: dict[str, Decimal]
) -> list[Factor]:
    if revenue_snapshot is None or market_cap <= 0:
        return [
            Factor(
                name=name, weight=weights[name], normalized_value=None, raw_value=None,
                insufficient_data=True,
                note="No RevenueSnapshot — protocol may take no fee cut, or isn't a matched DeFi protocol.",
            )
            for name in ["revenue_to_market_cap", "market_cap_to_revenue", "fdv_to_revenue"]
        ]

    annualized_revenue = revenue_snapshot.revenue_24h_usd * Decimal("365")
    factors = [
        Factor(
            name="revenue_to_market_cap",
            weight=weights["revenue_to_market_cap"],
            normalized_value=_score_ratio(annualized_revenue, market_cap, HEALTHY_REVENUE_TO_MC_RATIO),
            raw_value=f"~${annualized_revenue:,.0f}/yr revenue (from 24h figure) vs MC ${market_cap:,.0f}",
            note="Annualized from DefiLlama's 24h revenue figure (x365) — noisier than the 30D-based fee figure.",
        )
    ]

    if annualized_revenue <= 0:
        for name in ["market_cap_to_revenue", "fdv_to_revenue"]:
            factors.append(
                Factor(
                    name=name, weight=weights[name], normalized_value=None, raw_value=None,
                    insufficient_data=True, note="Annualized revenue is zero or negative.",
                )
            )
        return factors

    mc_to_rev = market_cap / annualized_revenue
    factors.append(
        Factor(
            name="market_cap_to_revenue", weight=weights["market_cap_to_revenue"],
            normalized_value=_score_multiple(mc_to_rev),
            raw_value=f"{mc_to_rev:.1f}x annualized revenue",
            note="Lower multiple (cheaper relative to revenue) scores higher.",
        )
    )

    if fdv is not None and fdv > 0:
        fdv_to_rev = fdv / annualized_revenue
        factors.append(
            Factor(
                name="fdv_to_revenue", weight=weights["fdv_to_revenue"],
                normalized_value=_score_multiple(fdv_to_rev),
                raw_value=f"{fdv_to_rev:.1f}x annualized revenue (FDV basis)",
                note="Lower multiple (cheaper relative to revenue) scores higher.",
            )
        )
    else:
        factors.append(
            Factor(
                name="fdv_to_revenue", weight=weights["fdv_to_revenue"],
                normalized_value=None, raw_value=None, insufficient_data=True,
                note="No FDV reported by provider for this asset.",
            )
        )

    return factors


def compute_undervaluation_score(asset: Asset, current: MarketSnapshot) -> ScoreResult:
    if current.asset_id != asset.id:
        raise ValueError("snapshot does not belong to asset")

    market_cap = current.market_cap_usd or Decimal("0")
    tvl_snapshot = _latest_tvl_snapshot(asset)
    fee_snapshot = _latest_fee_snapshot(asset)
    revenue_snapshot = _latest_revenue_snapshot(asset)

    sector_relevant = is_tvl_relevant(asset.sector)
    weights = _effective_weights(asset.sector)

    factors: list[Factor] = [
        _tvl_factor(tvl_snapshot, market_cap, weights["tvl_to_market_cap"], sector_relevant),
        _fees_factor(fee_snapshot, market_cap, weights["fees_to_market_cap"]),
        *_revenue_factors(revenue_snapshot, market_cap, current.fully_diluted_valuation_usd, weights),
    ]

    if not sector_relevant:
        factors.append(
            Factor(
                name="fundamentals_growth", weight=weights["fundamentals_growth"],
                normalized_value=None, raw_value=None, insufficient_data=True,
                note=_NOT_APPLICABLE_SECTOR_NOTE,
            )
        )
    else:
        tvl_growth_score = _score_tvl_growth(tvl_snapshot.change_7d_pct) if tvl_snapshot else None
        factors.append(
            Factor(
                name="fundamentals_growth",
                weight=weights["fundamentals_growth"],
                normalized_value=tvl_growth_score,
                raw_value=(
                    f"7D TVL change {tvl_snapshot.change_7d_pct:.2f}%" if tvl_growth_score is not None else None
                ),
                insufficient_data=tvl_growth_score is None,
                note="TVL-growth component only — revenue/fee/user growth components not yet added.",
            )
        )

    factors.append(
        Factor(
            name="user_activity_to_market_cap",
            weight=weights["user_activity_to_market_cap"],
            normalized_value=None, raw_value=None, insufficient_data=True,
            note=_NOT_YET_IMPLEMENTED_NOTE,
        )
    )

    return compute_weighted_score(model_name=MODEL_NAME, model_version=MODEL_VERSION, factors=factors)
