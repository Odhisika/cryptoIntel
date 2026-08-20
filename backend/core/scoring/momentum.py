"""
Momentum Score, model v1.1 (relative_strength filled via Binance data).

Per section 16: momentum must be kept separate from fundamental value — a
great project can have terrible short-term momentum and vice versa. This
score never looks at valuation/fundamentals data; it only looks at
price/volume history, which is the one thing Phase 1 actually has in
depth (append-only MarketSnapshot history).

v1.1 weights, and why:

- Return 7D/30D/90D (20/25/15): shorter windows weighted slightly higher
  than the 90D window because momentum, by definition, is about recent
  change; 90D is included mainly to distinguish "just started moving" from
  "already been running for months," which matters for a 10X thesis but
  isn't itself weighted as heavily as the more immediate windows.
- Volume change (25): a price move without a volume increase is far less
  reliable, so this carries real weight rather than being a minor
  modifier — deliberately close to the 30D price-return weight.
- Relative strength vs BTC/ETH and vs sector (15, split 5/5/5):
  a token that outperforms BTC AND its sector peers is demonstrating
  genuine demand independent of market-wide drift. Each sub-signal is
  independently computed; if one is missing (e.g. no sector data), its
  weight is redistributed to the others via the scoring framework's
  standard renormalization.

Returns are computed from whatever snapshots exist — if there's no
snapshot ~7/30/90 days back (e.g. an asset only just started being
tracked), that window is insufficient_data rather than approximated from
a mismatched snapshot, since a return computed against the wrong baseline
is worse than no number at all.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Optional

from core.models import Asset, MarketRegimeSnapshot, MarketSnapshot
from core.scoring.base import Factor, ScoreResult, compute_weighted_score
from core.scoring.snapshot_lookup import find_baseline_snapshot

MODEL_NAME = "momentum"
MODEL_VERSION = "v1.1"

WEIGHTS = {
    "return_7d": Decimal("20"),
    "return_30d": Decimal("25"),
    "return_90d": Decimal("15"),
    "volume_change_30d": Decimal("25"),
    "relative_strength": Decimal("15"),
}


def _return_pct(current: Decimal, baseline: Decimal) -> Optional[Decimal]:
    if baseline is None or baseline <= 0:
        return None
    return ((current - baseline) / baseline) * Decimal("100")


def _normalize_return(return_pct: Decimal) -> Decimal:
    """Map an arbitrary % return to 0-100. 0% return -> 50 (neutral),
    +100% -> 100 (capped), -50% -> 0 (capped). Linear between those points,
    deliberately not symmetric (crypto returns are right-skewed — a +100%
    move is common, a -100% move is impossible since price floors at 0)."""
    if return_pct >= Decimal("100"):
        return Decimal("100")
    if return_pct <= Decimal("-50"):
        return Decimal("0")
    if return_pct >= 0:
        return Decimal("50") + (return_pct / Decimal("100")) * Decimal("50")
    return Decimal("50") + (return_pct / Decimal("50")) * Decimal("50")


def _score_vs_btc(token_return_7d: Decimal, btc_return_7d: Decimal) -> Decimal:
    """Score relative to BTC: outperformance -> above 50, underperformance
    -> below 50. A +20% token return vs +5% BTC = strong outperformance.
    A +5% token return vs +20% BTC = underperformance despite positive
    absolute return."""
    diff = token_return_7d - btc_return_7d
    # +20% outperformance -> 100, -20% underperformance -> 0, 0% = 50
    normalized = min(max(diff, Decimal("-20")), Decimal("20"))
    if normalized >= 0:
        return Decimal("50") + (normalized / Decimal("20")) * Decimal("50")
    return Decimal("50") + (normalized / Decimal("20")) * Decimal("50")


def _score_vs_eth(token_return_7d: Decimal, eth_return_7d: Decimal) -> Decimal:
    """Same logic as vs BTC, but compared to ETH — captures whether the
    token is outperforming the broader smart-contract-platform market."""
    diff = token_return_7d - eth_return_7d
    normalized = min(max(diff, Decimal("-20")), Decimal("20"))
    if normalized >= 0:
        return Decimal("50") + (normalized / Decimal("20")) * Decimal("50")
    return Decimal("50") + (normalized / Decimal("20")) * Decimal("50")


def _score_vs_sector(token_return_7d: Decimal, sector_median_return_7d: Decimal) -> Decimal:
    """Score relative to sector median — are peers also moving? If the
    sector median is +10% and this token is +15%, it's a mild outperformer
    within an already-hot sector."""
    diff = token_return_7d - sector_median_return_7d
    normalized = min(max(diff, Decimal("-20")), Decimal("20"))
    if normalized >= 0:
        return Decimal("50") + (normalized / Decimal("20")) * Decimal("50")
    return Decimal("50") + (normalized / Decimal("20")) * Decimal("50")


def _latest_regime_snapshot() -> Optional[MarketRegimeSnapshot]:
    """Get the most recent market regime snapshot for BTC/ETH reference."""
    return MarketRegimeSnapshot.objects.order_by("-observed_at").first()


def compute_momentum_score(asset: Asset, current: MarketSnapshot) -> ScoreResult:
    if current.asset_id != asset.id:
        raise ValueError("snapshot does not belong to asset")

    factors: list[Factor] = []

    for label, days, weight_key in [("return_7d", 7, "return_7d"), ("return_30d", 30, "return_30d"),
                                     ("return_90d", 90, "return_90d")]:
        baseline = find_baseline_snapshot(asset, current.observed_at, days)
        if baseline is None:
            factors.append(
                Factor(
                    name=label,
                    weight=WEIGHTS[weight_key],
                    normalized_value=None,
                    raw_value=None,
                    insufficient_data=True,
                    note=f"No snapshot found ~{days}D back within tolerance.",
                )
            )
            continue

        pct = _return_pct(current.price_usd, baseline.price_usd)
        factors.append(
            Factor(
                name=label,
                weight=WEIGHTS[weight_key],
                normalized_value=_normalize_return(pct) if pct is not None else None,
                raw_value=f"{pct:.2f}%" if pct is not None else None,
                insufficient_data=pct is None,
                note=f"Price return vs snapshot at {baseline.observed_at.isoformat()}.",
            )
        )

    volume_baseline = find_baseline_snapshot(asset, current.observed_at, 30)
    if volume_baseline and current.volume_24h_usd and volume_baseline.volume_24h_usd:
        vol_pct = _return_pct(current.volume_24h_usd, volume_baseline.volume_24h_usd)
        factors.append(
            Factor(
                name="volume_change_30d",
                weight=WEIGHTS["volume_change_30d"],
                normalized_value=_normalize_return(vol_pct) if vol_pct is not None else None,
                raw_value=f"{vol_pct:.2f}%" if vol_pct is not None else None,
                insufficient_data=vol_pct is None,
                note="30D change in 24h trading volume.",
            )
        )
    else:
        factors.append(
            Factor(
                name="volume_change_30d",
                weight=WEIGHTS["volume_change_30d"],
                normalized_value=None,
                raw_value=None,
                insufficient_data=True,
                note="Missing current or baseline volume data.",
            )
        )

    # Relative strength vs BTC/ETH/sector — now filled via Binance data
    # (MarketRegimeSnapshot) and sector median. Each sub-signal is weighted
    # 5 points (total 15). Missing sub-signals are independently
    # insufficient_data and renormalized by the scoring framework.
    regime = _latest_regime_snapshot()
    token_7d_baseline = find_baseline_snapshot(asset, current.observed_at, 7)

    if token_7d_baseline is None:
        factors.append(
            Factor(
                name="relative_strength",
                weight=WEIGHTS["relative_strength"],
                normalized_value=None,
                raw_value=None,
                insufficient_data=True,
                note="No ~7D baseline snapshot available for relative-strength computation.",
            )
        )
    elif regime is None:
        factors.append(
            Factor(
                name="relative_strength",
                weight=WEIGHTS["relative_strength"],
                normalized_value=None,
                raw_value=None,
                insufficient_data=True,
                note="No MarketRegimeSnapshot available — BTC/ETH reference data missing.",
            )
        )
    else:
        token_7d_return = _return_pct(current.price_usd, token_7d_baseline.price_usd)
        if token_7d_return is None:
            factors.append(
                Factor(
                    name="relative_strength",
                    weight=WEIGHTS["relative_strength"],
                    normalized_value=None,
                    raw_value=None,
                    insufficient_data=True,
                    note="Could not compute token's 7D return for relative strength.",
                )
            )
        else:
            # Score vs BTC using regime's BTC 7D return
            btc_score = None
            if regime.btc_change_7d_pct is not None:
                btc_score = _score_vs_btc(token_7d_return, regime.btc_change_7d_pct)

            # Score vs ETH using regime's ETH 7D return
            eth_score = None
            if regime.eth_change_7d_pct is not None:
                eth_score = _score_vs_eth(token_7d_return, regime.eth_change_7d_pct)

            # Score vs sector median (from narrative engine if available)
            sector_score = None
            sector_note = ""
            if asset.sector:
                from core.scoring.narrative import compute_sector_narrative
                sector_snap = compute_sector_narrative(asset.sector)
                if sector_snap.asset_count >= 3 and sector_snap.median_momentum_score is not None:
                    # Convert sector median momentum score (0-100) back to
                    # an approximate return for comparison — crude but
                    # directionally useful. A median momentum of 60 implies
                    # the sector's median 7D return is roughly +10%.
                    # This is a heuristic, not an exact conversion.
                    sector_approx_return = (sector_snap.median_momentum_score - Decimal("50")) / Decimal("2.5")
                    sector_score = _score_vs_sector(token_7d_return, sector_approx_return)
                    sector_note = (
                        f"Sector '{asset.sector}' median momentum = {sector_snap.median_momentum_score} "
                        f"(approx return {sector_approx_return:.1f}%, n={sector_snap.asset_count})."
                    )

            # Combine the three sub-signals into one 15-point factor
            sub_scores = [s for s in [btc_score, eth_score, sector_score] if s is not None]
            if sub_scores:
                combined = sum(sub_scores) / Decimal(len(sub_scores))
            else:
                combined = None

            parts = []
            if btc_score is not None:
                parts.append(f"vs BTC: {btc_score:.0f}/100")
            if eth_score is not None:
                parts.append(f"vs ETH: {eth_score:.0f}/100")
            if sector_score is not None:
                parts.append(f"vs sector: {sector_score:.0f}/100")

            factors.append(
                Factor(
                    name="relative_strength",
                    weight=WEIGHTS["relative_strength"],
                    normalized_value=combined,
                    raw_value="; ".join(parts) if parts else None,
                    insufficient_data=combined is None,
                    note=(
                        "7D return vs BTC/ETH/sector. "
                        + (f"BTC 7D return: {regime.btc_change_7d_pct:.1f}%. " if regime.btc_change_7d_pct is not None else "")
                        + (f"ETH 7D return: {regime.eth_change_7d_pct:.1f}%. " if regime.eth_change_7d_pct is not None else "")
                        + (sector_note if sector_note else "Sector median not available (fewer than 3 peers with data).")
                    ),
                )
            )

    return compute_weighted_score(model_name=MODEL_NAME, model_version=MODEL_VERSION, factors=factors)
