"""
10X Potential Score, model v1.7 (DEX activity + market regime multiplier added).

As of v1.7, 10 of 10 spec'd factors are computable: Market-Cap
Opportunity, Valuation, Liquidity (Phase 2), Fundamentals (Phase 3.1),
On-Chain Adoption (Phase 3.2), Tokenomics (Phase 4), Developer Activity
(Phase 6), Narrative/Sector (Phase 8), Catalysts (Phase 8), and now
DEX Activity (v1.7, via DEX Screener data). Only Growth remains fully
insufficient_data, pending a data source not yet identified.

NEW IN v1.7:
- dex_activity factor (5 points): DEX liquidity depth, buy/sell
  pressure, token age, and multi-DEX presence. Fills the critical gap
  for evaluating new/small tokens that don't have CoinGecko coverage
  yet but ARE trading on DEXes.
- market_regime multiplier: a final score multiplier based on
  BTC/ETH trend context from Binance. +5% bonus in bullish regimes,
  -5% penalty in bearish regimes. Applied AFTER the weighted score
  is computed — it's market context, not a prediction.

SECTOR-AWARE WEIGHTING (Phase 7, section 20): for assets outside
core.scoring.sector_weights.TVL_RELEVANT_SECTORS, the `fundamentals`
factor (TVL/market-cap based, see _score_fundamentals()) is zero-weighted
and its weight redistributed proportionally across the other factors.

Weights v1.7 — growth reduced from 15 to 10 to make room for dex_activity:
- Market-Cap Opportunity (15), Fundamentals (15), Growth (10),
  Tokenomics (15), Valuation (10), Liquidity (10), DEX Activity (5),
  Narrative/Sector (5), Developer Activity (5), On-Chain Adoption (5),
  Catalysts (5). Total = 100.
"""

import math
from decimal import Decimal
from typing import Optional

from core.models import (
    Asset, Catalyst, DEXPairSnapshot, HolderSnapshot,
    MarketRegimeSnapshot, MarketSnapshot, Protocol,
)
from core.scoring.base import Factor, ScoreResult, compute_weighted_score
from core.scoring.sector_weights import is_tvl_relevant, redistribute_weights
from core.scoring.tokenomics_math import compute_supply_inflation_pct, compute_supply_ratios

MODEL_NAME = "10x_potential"
MODEL_VERSION = "v1.7"

WEIGHTS = {
    "market_cap_opportunity": Decimal("15"),
    "fundamentals": Decimal("15"),
    "growth": Decimal("10"),
    "tokenomics": Decimal("15"),
    "valuation": Decimal("10"),
    "liquidity": Decimal("10"),
    "dex_activity": Decimal("5"),
    "narrative_sector": Decimal("5"),
    "developer_activity": Decimal("5"),
    "onchain_adoption": Decimal("5"),
    "catalysts": Decimal("5"),
}

_NOT_APPLICABLE_SECTOR_NOTE = (
    "Not applicable for this asset's sector — TVL isn't a meaningful metric for this asset type; "
    "weight redistributed to other factors."
)

UNIVERSE_MIN_MARKET_CAP = Decimal("10000000")
UNIVERSE_MAX_MARKET_CAP = Decimal("2000000000")


def _score_market_cap_opportunity(market_cap_usd: Decimal) -> Decimal:
    span = UNIVERSE_MAX_MARKET_CAP - UNIVERSE_MIN_MARKET_CAP
    if span <= 0:
        return Decimal("50")
    position = (market_cap_usd - UNIVERSE_MIN_MARKET_CAP) / span
    position = max(Decimal("0"), min(Decimal("1"), position))
    return (Decimal("1") - position) * Decimal("100")


def _score_valuation(market_cap_usd: Decimal, fdv_usd: Optional[Decimal]) -> Optional[Decimal]:
    if fdv_usd is None or fdv_usd <= 0:
        return None
    if fdv_usd <= market_cap_usd:
        return Decimal("100")
    gap_ratio = (fdv_usd - market_cap_usd) / fdv_usd
    return (Decimal("1") - gap_ratio) * Decimal("100")


def _score_liquidity(market_cap_usd: Decimal, volume_24h_usd: Optional[Decimal]) -> Optional[Decimal]:
    if volume_24h_usd is None or market_cap_usd <= 0:
        return None
    ratio = volume_24h_usd / market_cap_usd
    healthy_ratio = Decimal("0.05")
    normalized = min(ratio / healthy_ratio, Decimal("1")) * Decimal("100")
    return normalized


FUNDAMENTALS_HEALTHY_TVL_RATIO = Decimal("2.0")


def _score_fundamentals(asset: Asset, snapshot: MarketSnapshot) -> tuple[Optional[Decimal], Optional[str], str]:
    protocol = Protocol.objects.filter(asset=asset, is_active=True).first()
    if protocol is None:
        return None, None, "No matched DeFi protocol (asset may not be a DeFi protocol) — no TVL-based signal."

    tvl_snapshot = protocol.tvl_snapshots.order_by("-observed_at").first()
    if tvl_snapshot is None or not snapshot.market_cap_usd or snapshot.market_cap_usd <= 0:
        return None, None, "Protocol matched but no TVL snapshot available yet."

    ratio = tvl_snapshot.tvl_usd / snapshot.market_cap_usd
    score = min(ratio / FUNDAMENTALS_HEALTHY_TVL_RATIO, Decimal("1")) * Decimal("100")
    raw = f"TVL ${tvl_snapshot.tvl_usd:,.0f} vs MC ${snapshot.market_cap_usd:,.0f}"
    note = "TVL/market-cap only — revenue, dev activity, and on-chain adoption not yet factored in."
    return score, raw, note


_HOLDER_COUNT_FLOOR = 100
_HOLDER_COUNT_CEILING = 100_000


def _score_onchain_adoption(asset: Asset) -> tuple[Optional[Decimal], Optional[str], str]:
    latest = (
        HolderSnapshot.objects.filter(contract_address__asset=asset, holder_count__isnull=False)
        .order_by("-observed_at")
        .first()
    )
    if latest is None:
        return None, None, "No holder-count data available for this asset (Phase 3.2, coverage varies)."

    count = latest.holder_count
    if count <= _HOLDER_COUNT_FLOOR:
        score = Decimal("0")
    elif count >= _HOLDER_COUNT_CEILING:
        score = Decimal("100")
    else:
        log_position = (math.log10(count) - math.log10(_HOLDER_COUNT_FLOOR)) / (
            math.log10(_HOLDER_COUNT_CEILING) - math.log10(_HOLDER_COUNT_FLOOR)
        )
        score = Decimal(str(log_position)) * Decimal("100")

    return score, f"{count:,} holders", "Log-scale normalized holder count from CoinGecko on-chain data."


_INFLATION_PENALTY_CAP = Decimal("40")


def _score_tokenomics(asset: Asset, snapshot: MarketSnapshot) -> tuple[Optional[Decimal], Optional[str], str]:
    ratios = compute_supply_ratios(snapshot)
    if ratios.circulating_to_max_pct is None:
        return None, None, "No max_supply reported by provider for this asset — cannot compute circ/max ratio."

    score = ratios.circulating_to_max_pct
    raw_parts = [f"circ/max supply = {ratios.circulating_to_max_pct:.1f}%"]

    inflation_12m = compute_supply_inflation_pct(asset, snapshot, months_back=12)
    if inflation_12m is not None and inflation_12m > 0:
        penalty = min(inflation_12m, _INFLATION_PENALTY_CAP)
        score = max(score - penalty, Decimal("0"))
        raw_parts.append(f"12M supply inflation = {inflation_12m:.1f}%")
        note = "circ/max ratio, adjusted down for realized 12M supply inflation."
    else:
        note = (
            "circ/max ratio only — 12M inflation data not yet available (needs 12+ months of tracking history)."
        )

    return score, "; ".join(raw_parts), note


_COMMITS_4W_CEILING = 100


def _score_developer_activity(asset: Asset) -> tuple[Optional[Decimal], Optional[str], str]:
    from core.models import DeveloperActivitySnapshot

    latest = (
        DeveloperActivitySnapshot.objects.filter(asset=asset).order_by("-observed_at").first()
    )
    if latest is None:
        return None, None, "No GitHub repo linked or no developer-activity data ingested yet."

    if latest.is_archived:
        return Decimal("0"), "Repository is archived", "Archived repos score 0 regardless of historical activity."

    if latest.commits_4w is None:
        return None, None, "Repo linked but commit-activity stats not yet available from GitHub (may need a retry)."

    commits = latest.commits_4w
    if commits <= 0:
        score = Decimal("0")
    elif commits >= _COMMITS_4W_CEILING:
        score = Decimal("100")
    else:
        score = (Decimal(commits) / Decimal(_COMMITS_4W_CEILING)) * Decimal("100")

    return score, f"{commits} commits in last 4 weeks", "Linear-scaled recent commit count from GitHub."


def _effective_weights(sector) -> dict[str, Decimal]:
    if is_tvl_relevant(sector):
        return WEIGHTS
    return redistribute_weights(WEIGHTS, {"fundamentals"})


_IMPACT_SCORES = {"high": Decimal("100"), "medium": Decimal("60"), "low": Decimal("30")}
_CONFIDENCE_MULTIPLIERS = {"confirmed": Decimal("1.0"), "likely": Decimal("0.7"), "speculative": Decimal("0.3")}


def _score_catalysts(asset: Asset) -> tuple[Optional[Decimal], Optional[str], str]:
    from datetime import date

    upcoming = (
        Catalyst.objects.filter(
            asset=asset, event_date__gte=date.today(), status__in=[Catalyst.Status.UPCOMING, Catalyst.Status.CONFIRMED]
        )
        .order_by("event_date")
        .first()
    )
    if upcoming is None:
        return None, None, "No curated catalyst on record for this asset (absence is not evidence of no catalyst)."

    score = _IMPACT_SCORES[upcoming.impact_estimate] * _CONFIDENCE_MULTIPLIERS[upcoming.confidence]
    raw = f"'{upcoming.title}' on {upcoming.event_date} ({upcoming.impact_estimate} impact, {upcoming.confidence})"
    note = "Nearest upcoming manually-curated catalyst's impact x confidence. Coverage is curator-limited, not comprehensive."
    return score, raw, note


def _score_narrative_sector(asset: Asset) -> tuple[Optional[Decimal], Optional[str], str]:
    if not asset.sector:
        return None, None, "Asset has no sector classification — cannot compute a sector-level narrative signal."

    from core.scoring.narrative import compute_sector_narrative

    snapshot = compute_sector_narrative(asset.sector)
    if snapshot.asset_count < 3 or snapshot.median_momentum_score is None:
        return None, None, (
            f"Fewer than 3 classified assets with momentum data in sector '{asset.sector}' "
            f"({snapshot.asset_count} found) — median would be unreliable."
        )

    return (
        snapshot.median_momentum_score,
        f"Sector '{asset.sector}' median Momentum Score = {snapshot.median_momentum_score} "
        f"(n={snapshot.asset_count})",
        "Price/momentum-based sector proxy only — not social attention, search interest, or funding data "
        "(no free source found for those, per Phase 8 research).",
    )


# DEX activity scoring thresholds — placeholders, same caveat as every
# other threshold in this codebase: needs Phase 10 validation.
_DEX_HEALTHY_LIQUIDITY_RATIO = Decimal("0.05")
_TOKEN_AGE_PENALTY_DAYS = 7
_TOKEN_AGE_SAFE_DAYS = 30


def _score_dex_activity(asset: Asset, snapshot: MarketSnapshot) -> tuple[Optional[Decimal], Optional[str], str]:
    """DEX activity signal from DEX Screener data: liquidity depth,
    buy/sell pressure, token age, and multi-DEX presence.

    This is the critical factor for gem detection — it captures on-chain
    trading activity that CoinGecko alone misses for new/small tokens."""

    dex_snap = (
        DEXPairSnapshot.objects.filter(asset=asset)
        .order_by("-observed_at")
        .first()
    )
    if dex_snap is None:
        return None, None, "No DEX Screener data available for this asset — may not be trading on DEXes yet."

    sub_scores = []
    raw_parts = []

    # 1. DEX liquidity health (30 points)
    if snapshot.market_cap_usd and snapshot.market_cap_usd > 0 and dex_snap.liquidity_usd > 0:
        liq_ratio = dex_snap.liquidity_usd / snapshot.market_cap_usd
        liq_score = min(liq_ratio / _DEX_HEALTHY_LIQUIDITY_RATIO, Decimal("1")) * Decimal("100")
        sub_scores.append(("liquidity", Decimal("30"), liq_score))
        raw_parts.append(f"DEX liq ${dex_snap.liquidity_usd:,.0f} / MC ${snapshot.market_cap_usd:,.0f} = {liq_ratio:.2%}")
    else:
        sub_scores.append(("liquidity", Decimal("30"), None))

    # 2. Buy/sell ratio (25 points) — healthy tokens have >40% buys
    if dex_snap.txns_24h_buys is not None and dex_snap.txns_24h_sells is not None:
        total_txns = dex_snap.txns_24h_buys + dex_snap.txns_24h_sells
        if total_txns > 0:
            buy_pct = Decimal(dex_snap.txns_24h_buys) / Decimal(total_txns)
            buy_score = min(max((buy_pct - Decimal("0.3")) / Decimal("0.4"), Decimal("0")), Decimal("1")) * Decimal("100")
            sub_scores.append(("buy_ratio", Decimal("25"), buy_score))
            raw_parts.append(f"Buys: {dex_snap.txns_24h_buys} / Sells: {dex_snap.txns_24h_sells} ({buy_pct:.0%} buys)")
        else:
            sub_scores.append(("buy_ratio", Decimal("25"), None))
    else:
        sub_scores.append(("buy_ratio", Decimal("25"), None))

    # 3. Token age (20 points) — very new tokens get penalized for rug risk
    if dex_snap.earliest_pair_created_at:
        age_days = (snapshot.observed_at - dex_snap.earliest_pair_created_at).days
        if age_days < _TOKEN_AGE_PENALTY_DAYS:
            age_score = Decimal(str(age_days)) / Decimal(str(_TOKEN_AGE_PENALTY_DAYS)) * Decimal("60")
        elif age_days >= _TOKEN_AGE_SAFE_DAYS:
            age_score = Decimal("100")
        else:
            age_score = Decimal("60") + (
                (Decimal(str(age_days)) - Decimal(str(_TOKEN_AGE_PENALTY_DAYS)))
                / (Decimal(str(_TOKEN_AGE_SAFE_DAYS)) - Decimal(str(_TOKEN_AGE_PENALTY_DAYS)))
            ) * Decimal("40")
        sub_scores.append(("age", Decimal("20"), age_score))
        raw_parts.append(f"Token age: {age_days} days")
    else:
        sub_scores.append(("age", Decimal("20"), None))

    # 4. Multi-DEX presence (25 points) — tokens on 2+ DEXes score higher
    if dex_snap.pair_count and dex_snap.pair_count > 0:
        if dex_snap.pair_count >= 3:
            dex_score = Decimal("100")
        elif dex_snap.pair_count == 2:
            dex_score = Decimal("70")
        else:
            dex_score = Decimal("40")
        sub_scores.append(("multi_dex", Decimal("25"), dex_score))
        raw_parts.append(f"{dex_snap.pair_count} DEX pairs across {', '.join(dex_snap.chains or ['?'])}")
    else:
        sub_scores.append(("multi_dex", Decimal("25"), None))

    available = [(name, w, v) for name, w, v in sub_scores if v is not None]
    if not available:
        return None, None, "DEX data present but all sub-signals are None."

    total_w = sum(w for _, w, _ in available)
    if total_w <= 0:
        return None, None, "DEX activity sub-scores have no weight."

    weighted_sum = sum(w * v for _, w, v in available)
    score = (weighted_sum / total_w).quantize(Decimal("0.01"))
    raw = "; ".join(raw_parts)
    note = (
        "DEX liquidity health, buy/sell ratio, token age, and multi-DEX presence. "
        f"Sub-scores from {len(available)}/4 available signals."
    )
    return score, raw, note


# Market regime multiplier — applied as a final adjustment, not a factor.
_REGIME_MULTIPLIERS = {
    "bullish": Decimal("1.05"),
    "bearish": Decimal("0.95"),
    "neutral": Decimal("1.00"),
}


def _apply_regime_multiplier(score: Decimal) -> Decimal:
    regime_snap = MarketRegimeSnapshot.objects.order_by("-observed_at").first()
    if regime_snap is None:
        return score

    multiplier = _REGIME_MULTIPLIERS.get(regime_snap.regime, Decimal("1.00"))
    adjusted = (score * multiplier).quantize(Decimal("0.01"))
    return max(Decimal("0"), min(Decimal("100"), adjusted))


def compute_10x_potential_score(asset: Asset, snapshot: MarketSnapshot) -> ScoreResult:
    if snapshot.asset_id != asset.id:
        raise ValueError("snapshot does not belong to asset")

    sector_relevant = is_tvl_relevant(asset.sector)
    weights = _effective_weights(asset.sector)

    factors: list[Factor] = []

    factors.append(
        Factor(
            name="market_cap_opportunity",
            weight=weights["market_cap_opportunity"],
            normalized_value=_score_market_cap_opportunity(snapshot.market_cap_usd or Decimal("0")),
            raw_value=f"${snapshot.market_cap_usd:,.0f}" if snapshot.market_cap_usd else None,
            note="Smaller market cap within the scanned universe band scores higher.",
        )
        if snapshot.market_cap_usd
        else Factor(
            name="market_cap_opportunity",
            weight=weights["market_cap_opportunity"],
            normalized_value=None,
            raw_value=None,
            insufficient_data=True,
            note="No market cap on this snapshot.",
        )
    )

    valuation_score = _score_valuation(
        snapshot.market_cap_usd or Decimal("0"), snapshot.fully_diluted_valuation_usd
    )
    factors.append(
        Factor(
            name="valuation",
            weight=weights["valuation"],
            normalized_value=valuation_score,
            raw_value=(
                f"FDV ${snapshot.fully_diluted_valuation_usd:,.0f} vs MC ${snapshot.market_cap_usd:,.0f}"
                if valuation_score is not None
                else None
            ),
            insufficient_data=valuation_score is None,
            note="Lower FDV/market-cap gap (less dilution overhang) scores higher."
            if valuation_score is not None
            else "No FDV reported by provider for this asset.",
        )
    )

    liquidity_score = _score_liquidity(snapshot.market_cap_usd or Decimal("0"), snapshot.volume_24h_usd)
    factors.append(
        Factor(
            name="liquidity",
            weight=weights["liquidity"],
            normalized_value=liquidity_score,
            raw_value=f"24h vol ${snapshot.volume_24h_usd:,.0f}" if snapshot.volume_24h_usd else None,
            insufficient_data=liquidity_score is None,
            note="24h volume / market cap ratio; very low liquidity is a headwind, not a positive signal."
            if liquidity_score is not None
            else "No volume reported by provider for this asset.",
        )
    )

    # Growth factor — reduced from 15 to 10 in v1.7
    factors.append(
        Factor(
            name="growth",
            weight=weights["growth"],
            normalized_value=None,
            raw_value=None,
            insufficient_data=True,
            note="Not yet implemented — awaiting a reliable growth data source.",
        )
    )

    if not sector_relevant:
        factors.append(
            Factor(
                name="fundamentals", weight=weights["fundamentals"],
                normalized_value=None, raw_value=None, insufficient_data=True,
                note=_NOT_APPLICABLE_SECTOR_NOTE,
            )
        )
    else:
        fundamentals_score, fundamentals_raw, fundamentals_note = _score_fundamentals(asset, snapshot)
        factors.append(
            Factor(
                name="fundamentals",
                weight=weights["fundamentals"],
                normalized_value=fundamentals_score,
                raw_value=fundamentals_raw,
                insufficient_data=fundamentals_score is None,
                note=fundamentals_note,
            )
        )

    adoption_score, adoption_raw, adoption_note = _score_onchain_adoption(asset)
    factors.append(
        Factor(
            name="onchain_adoption",
            weight=weights["onchain_adoption"],
            normalized_value=adoption_score,
            raw_value=adoption_raw,
            insufficient_data=adoption_score is None,
            note=adoption_note,
        )
    )

    tokenomics_score, tokenomics_raw, tokenomics_note = _score_tokenomics(asset, snapshot)
    factors.append(
        Factor(
            name="tokenomics",
            weight=weights["tokenomics"],
            normalized_value=tokenomics_score,
            raw_value=tokenomics_raw,
            insufficient_data=tokenomics_score is None,
            note=tokenomics_note,
        )
    )

    dev_score, dev_raw, dev_note = _score_developer_activity(asset)
    factors.append(
        Factor(
            name="developer_activity",
            weight=weights["developer_activity"],
            normalized_value=dev_score,
            raw_value=dev_raw,
            insufficient_data=dev_score is None,
            note=dev_note,
        )
    )

    narrative_score, narrative_raw, narrative_note = _score_narrative_sector(asset)
    factors.append(
        Factor(
            name="narrative_sector",
            weight=weights["narrative_sector"],
            normalized_value=narrative_score,
            raw_value=narrative_raw,
            insufficient_data=narrative_score is None,
            note=narrative_note,
        )
    )

    catalyst_score, catalyst_raw, catalyst_note = _score_catalysts(asset)
    factors.append(
        Factor(
            name="catalysts",
            weight=weights["catalysts"],
            normalized_value=catalyst_score,
            raw_value=catalyst_raw,
            insufficient_data=catalyst_score is None,
            note=catalyst_note,
        )
    )

    # NEW in v1.7: DEX activity factor
    dex_score, dex_raw, dex_note = _score_dex_activity(asset, snapshot)
    factors.append(
        Factor(
            name="dex_activity",
            weight=weights["dex_activity"],
            normalized_value=dex_score,
            raw_value=dex_raw,
            insufficient_data=dex_score is None,
            note=dex_note,
        )
    )

    result = compute_weighted_score(model_name=MODEL_NAME, model_version=MODEL_VERSION, factors=factors)

    # Apply market regime multiplier — market context, not a prediction.
    adjusted_score = _apply_regime_multiplier(result.score)

    return ScoreResult(
        model_name=result.model_name,
        model_version=result.model_version,
        score=adjusted_score,
        data_confidence=result.data_confidence,
        factors=result.factors,
    )
