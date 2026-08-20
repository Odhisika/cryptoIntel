"""
Risk Score, model v1.3 (DEX risk factor added).

Per section 17: risk is a SEPARATE score, never subtracted from 10X
Potential/Undervaluation/Momentum. This module produces a 0-100 risk score
where HIGHER = MORE RISK (this is the opposite convention from the other
three scores, where higher = better — documented loudly here because
getting this backwards in the UI would be a serious bug, not a cosmetic
one). `risk_category()` below maps the number to LOW/MEDIUM/HIGH for
display, matching the spec's own example format ("Risk: HIGH").

v1.3 factors and why:

- liquidity_risk (15): low 24h-volume/market-cap ratio = harder to exit
- dilution_risk (15): large FDV/MC or circ/max gap = future sell pressure
- volatility_30d (10): historical daily-return dispersion
- token_unlock_risk (15): insufficient_data (no free unlock data)
- whale_concentration_risk (10): top-10 address concentration %
- dex_risk (5): NEW — DEX-specific risk signals: low DEX liquidity
  relative to market cap, very new token (<7 days), single-DEX
  concentration, and low DEX volume/market cap ratio.
- smart_contract_risk (10): insufficient_data
- centralization_risk (5): insufficient_data
- governance_risk (5): insufficient_data
- protocol_dependency_risk (5): insufficient_data
- exchange_concentration_risk (5): insufficient_data
- project_age_risk (5): insufficient_data (partially covered by dex_risk
  now — very new tokens are flagged there)
"""

import statistics
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from core.models import Asset, DEXPairSnapshot, HolderSnapshot, MarketSnapshot
from core.scoring.base import Factor, ScoreResult, compute_weighted_score
from core.scoring.tokenomics_math import compute_supply_ratios

MODEL_NAME = "risk"
MODEL_VERSION = "v1.3"

WEIGHTS = {
    "liquidity_risk": Decimal("15"),
    "dilution_risk": Decimal("15"),
    "volatility_30d": Decimal("10"),
    "token_unlock_risk": Decimal("15"),
    "whale_concentration_risk": Decimal("10"),
    "dex_risk": Decimal("5"),
    "smart_contract_risk": Decimal("10"),
    "centralization_risk": Decimal("5"),
    "governance_risk": Decimal("5"),
    "protocol_dependency_risk": Decimal("5"),
    "exchange_concentration_risk": Decimal("5"),
}

MIN_SNAPSHOTS_FOR_VOLATILITY = 5


def risk_category(score: Decimal) -> str:
    if score < Decimal("33"):
        return "LOW"
    if score < Decimal("66"):
        return "MEDIUM"
    return "HIGH"


def _score_liquidity_risk(market_cap_usd: Decimal, volume_24h_usd: Optional[Decimal]) -> Optional[Decimal]:
    if volume_24h_usd is None or market_cap_usd <= 0:
        return None
    ratio = volume_24h_usd / market_cap_usd
    healthy_ratio = Decimal("0.05")
    liquidity_health = min(ratio / healthy_ratio, Decimal("1")) * Decimal("100")
    return Decimal("100") - liquidity_health


def _score_dilution_risk(snapshot: MarketSnapshot) -> tuple[Optional[Decimal], str]:
    """Two independent dilution signals, combined conservatively (higher
    risk wins) since they can disagree — see tokenomics_math.py's module
    docstring for why FDV/MC (price-weighted) and circ/max (supply-
    weighted) aren't redundant with each other:
    - FDV/MC gap: how much of the market's IMPLIED valuation isn't yet
      backed by circulating supply.
    - 100 - circ/max ratio: how much of the theoretical MAX supply hasn't
      entered circulation yet, independent of price.
    """
    market_cap_usd = snapshot.market_cap_usd or Decimal("0")
    fdv_usd = snapshot.fully_diluted_valuation_usd

    fdv_gap_risk = None
    if fdv_usd and fdv_usd > 0 and fdv_usd > market_cap_usd:
        fdv_gap_risk = ((fdv_usd - market_cap_usd) / fdv_usd) * Decimal("100")

    ratios = compute_supply_ratios(snapshot)
    supply_gap_risk = None
    if ratios.circulating_to_max_pct is not None:
        supply_gap_risk = Decimal("100") - ratios.circulating_to_max_pct

    candidates = [r for r in [fdv_gap_risk, supply_gap_risk] if r is not None]
    if not candidates:
        return None, "No FDV or max_supply data available."

    risk = max(candidates)
    if fdv_gap_risk is not None and supply_gap_risk is not None:
        note = f"max(FDV/MC gap={fdv_gap_risk:.1f}, supply gap={supply_gap_risk:.1f}) — more conservative of the two."
    elif fdv_gap_risk is not None:
        note = "FDV/MC gap only — no max_supply reported for the supply-side signal."
    else:
        note = "Supply gap (circ/max) only — no FDV reported for the price-side signal."
    return risk, note


def _score_volatility(asset: Asset, current: MarketSnapshot) -> tuple[Optional[Decimal], str]:
    window_start = current.observed_at - timedelta(days=30)
    prices = list(
        MarketSnapshot.objects.filter(
            asset=asset, observed_at__gte=window_start, observed_at__lte=current.observed_at
        )
        .order_by("observed_at")
        .values_list("price_usd", flat=True)
    )
    if len(prices) < MIN_SNAPSHOTS_FOR_VOLATILITY:
        return None, f"Only {len(prices)} snapshots in the last 30D — need at least {MIN_SNAPSHOTS_FOR_VOLATILITY}."

    returns = []
    for prev, curr in zip(prices, prices[1:]):
        if prev and prev > 0:
            returns.append(float((curr - prev) / prev))

    if len(returns) < MIN_SNAPSHOTS_FOR_VOLATILITY - 1:
        return None, "Not enough consecutive valid price points to compute return volatility."

    stdev = statistics.pstdev(returns)
    # Rough calibration: 2% stdev between consecutive ingested points
    # (~15 min apart) is treated as "very high" volatility (100). This
    # threshold is a placeholder, not a validated one — flag for
    # recalibration once Phase 10 backtesting exists.
    normalized = min(Decimal(str(stdev)) / Decimal("0.02"), Decimal("1")) * Decimal("100")
    return normalized, f"stdev of {len(returns)} period returns = {stdev:.4%}"


def _score_whale_concentration(asset: Asset) -> tuple[Optional[Decimal], Optional[str], str]:
    """top_10_concentration_pct straight from CoinGecko's on-chain holders
    data (Phase 3.2) — already a 0-100-ish percentage of supply held by
    the top 10 addresses, so it's used directly as the risk value rather
    than going through a separate normalization curve. Coverage is Beta
    per CoinGecko's own docs, so this stays insufficient_data for many
    assets, not just new/obscure ones."""

    latest = (
        HolderSnapshot.objects.filter(
            contract_address__asset=asset, top_10_concentration_pct__isnull=False
        )
        .order_by("-observed_at")
        .first()
    )
    if latest is None:
        return None, None, "No holder-concentration data available for this asset (Phase 3.2, coverage varies)."

    pct = min(latest.top_10_concentration_pct, Decimal("100"))
    return pct, f"Top 10 addresses hold {pct:.2f}% of supply", "From CoinGecko on-chain holders data (Beta coverage)."


# DEX risk thresholds — placeholders, same caveat as every other threshold.
_DEX_LOW_LIQUIDITY_RATIO = Decimal("0.01")  # liquidity/market_cap below this = high risk
_DEX_LOW_VOLUME_RATIO = Decimal("0.01")    # volume/market_cap below this = high risk
_DEX_NEW_TOKEN_DAYS = 7                    # younger than this = elevated risk


def _score_dex_risk(asset: Asset, snapshot: MarketSnapshot) -> tuple[Optional[Decimal], Optional[str], str]:
    """DEX-specific risk signals from DEX Screener data: low DEX liquidity
    relative to market cap, very new token, single-DEX concentration, and
    low DEX volume. These are RISK signals (higher = worse), separate from
    the dex_activity opportunity signal in the 10X Potential score."""

    dex_snap = (
        DEXPairSnapshot.objects.filter(asset=asset)
        .order_by("-observed_at")
        .first()
    )
    if dex_snap is None:
        return None, None, "No DEX Screener data — cannot assess DEX-specific risk."

    risk_signals = []
    raw_parts = []

    # 1. DEX liquidity risk: low liquidity relative to market cap
    if snapshot.market_cap_usd and snapshot.market_cap_usd > 0 and dex_snap.liquidity_usd > 0:
        liq_ratio = dex_snap.liquidity_usd / snapshot.market_cap_usd
        if liq_ratio < _DEX_LOW_LIQUIDITY_RATIO:
            risk_signals.append(Decimal("100"))  # very high risk
            raw_parts.append(f"Very low DEX liquidity: {liq_ratio:.2%} of MC")
        elif liq_ratio < _DEX_LOW_LIQUIDITY_RATIO * 3:
            risk_signals.append(Decimal("60"))
            raw_parts.append(f"Low DEX liquidity: {liq_ratio:.2%} of MC")
        else:
            risk_signals.append(Decimal("20"))
            raw_parts.append(f"DEX liquidity: {liq_ratio:.2%} of MC")

    # 2. Token age risk: very new tokens are higher risk
    if dex_snap.earliest_pair_created_at:
        age_days = (snapshot.observed_at - dex_snap.earliest_pair_created_at).days
        if age_days < _DEX_NEW_TOKEN_DAYS:
            risk_signals.append(Decimal("90"))
            raw_parts.append(f"Very new token: {age_days} days old")
        elif age_days < 30:
            risk_signals.append(Decimal("50"))
            raw_parts.append(f"New token: {age_days} days old")
        else:
            risk_signals.append(Decimal("10"))
            raw_parts.append(f"Token age: {age_days} days")

    # 3. Single-DEX concentration: tokens on only 1 DEX have higher risk
    if dex_snap.pair_count:
        if dex_snap.pair_count <= 1:
            risk_signals.append(Decimal("70"))
            raw_parts.append("Single DEX pair (no diversification)")
        elif dex_snap.pair_count == 2:
            risk_signals.append(Decimal("40"))
            raw_parts.append("2 DEX pairs")
        else:
            risk_signals.append(Decimal("10"))
            raw_parts.append(f"{dex_snap.pair_count} DEX pairs")

    # 4. DEX volume risk: very low volume/market cap ratio
    if dex_snap.volume_24h_usd and snapshot.market_cap_usd and snapshot.market_cap_usd > 0:
        vol_ratio = dex_snap.volume_24h_usd / snapshot.market_cap_usd
        if vol_ratio < _DEX_LOW_VOLUME_RATIO:
            risk_signals.append(Decimal("80"))
            raw_parts.append(f"Very low DEX volume: {vol_ratio:.2%} of MC")
        elif vol_ratio < _DEX_LOW_VOLUME_RATIO * 3:
            risk_signals.append(Decimal("40"))
            raw_parts.append(f"Low DEX volume: {vol_ratio:.2%} of MC")
        else:
            risk_signals.append(Decimal("10"))
            raw_parts.append(f"DEX volume: {vol_ratio:.2%} of MC")

    if not risk_signals:
        return None, None, "DEX data present but no risk signals could be computed."

    # Average across available risk signals (higher = more risk, consistent
    # with this module's convention)
    avg_risk = sum(risk_signals) / Decimal(len(risk_signals))
    avg_risk = avg_risk.quantize(Decimal("0.01"))
    raw = "; ".join(raw_parts)
    note = f"DEX-specific risk signals ({len(risk_signals)}/4 available): liquidity depth, token age, DEX concentration, volume."
    return avg_risk, raw, note


def compute_risk_score(asset: Asset, current: MarketSnapshot) -> ScoreResult:
    if current.asset_id != asset.id:
        raise ValueError("snapshot does not belong to asset")

    factors: list[Factor] = []

    liq_risk = _score_liquidity_risk(current.market_cap_usd or Decimal("0"), current.volume_24h_usd)
    factors.append(
        Factor(
            name="liquidity_risk",
            weight=WEIGHTS["liquidity_risk"],
            normalized_value=liq_risk,
            raw_value=f"24h vol ${current.volume_24h_usd:,.0f}" if current.volume_24h_usd else None,
            insufficient_data=liq_risk is None,
            note="Higher = harder to exit a position without moving the market."
            if liq_risk is not None
            else "No volume data available.",
        )
    )

    dilution_risk, dilution_note = _score_dilution_risk(current)
    factors.append(
        Factor(
            name="dilution_risk",
            weight=WEIGHTS["dilution_risk"],
            normalized_value=dilution_risk,
            raw_value=dilution_note if dilution_risk is not None else None,
            insufficient_data=dilution_risk is None,
            note=dilution_note,
        )
    )

    vol_score, vol_note = _score_volatility(asset, current)
    factors.append(
        Factor(
            name="volatility_30d",
            weight=WEIGHTS["volatility_30d"],
            normalized_value=vol_score,
            raw_value=vol_note if vol_score is not None else None,
            insufficient_data=vol_score is None,
            note=vol_note,
        )
    )

    factors.append(
        Factor(
            name="token_unlock_risk",
            weight=WEIGHTS["token_unlock_risk"],
            normalized_value=None,
            raw_value=None,
            insufficient_data=True,
            note="No free unlock-schedule data source found (Phase 4 research) — DefiLlama's /api/emissions "
            "and Tokenomist.ai's API both require a paid subscription. dilution_risk above covers the "
            "supply/FDV-ratio proxy signal; this factor specifically needs actual unlock event dates/amounts.",
        )
    )

    for name in ["smart_contract_risk", "centralization_risk", "governance_risk",
                 "protocol_dependency_risk", "exchange_concentration_risk"]:
        factors.append(
            Factor(
                name=name,
                weight=WEIGHTS[name],
                normalized_value=None,
                raw_value=None,
                insufficient_data=True,
                note="Not yet implemented — awaiting the phase that provides this data source.",
            )
        )

    whale_risk, whale_raw, whale_note = _score_whale_concentration(asset)
    factors.append(
        Factor(
            name="whale_concentration_risk",
            weight=WEIGHTS["whale_concentration_risk"],
            normalized_value=whale_risk,
            raw_value=whale_raw,
            insufficient_data=whale_risk is None,
            note=whale_note,
        )
    )

    # NEW in v1.3: DEX-specific risk signals
    dex_risk, dex_raw, dex_note = _score_dex_risk(asset, current)
    factors.append(
        Factor(
            name="dex_risk",
            weight=WEIGHTS["dex_risk"],
            normalized_value=dex_risk,
            raw_value=dex_raw,
            insufficient_data=dex_risk is None,
            note=dex_note,
        )
    )

    # project_age_risk retired in v1.3 — its token-age signal is now
    # covered by dex_risk (which has better data from DEX Screener).
    # Removed from WEIGHTS and this function.

    return compute_weighted_score(model_name=MODEL_NAME, model_version=MODEL_VERSION, factors=factors)
