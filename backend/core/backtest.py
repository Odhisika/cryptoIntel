"""
Historical score accuracy / backtesting (Roadmap Tier 1, Feature 3).

Answers the sales-pitch question: "what did the scanner predict, and how did
those tokens actually perform?" by comparing each ScoreSnapshot to the price
30/60/90 days later.

Methodology (deliberately simple and honest):
  - A score's baseline is the MarketSnapshot it was computed against
    (ScoreSnapshot.market_snapshot) — its price and observed_at.
  - Forward return = (price H days after baseline / baseline price) - 1, where
    "H days after" is the MarketSnapshot nearest to baseline.observed_at + H
    days (but never before the baseline).
  - Returns are bucketed by the risk/reward tier of the score, so we can report
    win rate / avg return / Sharpe PER TIER, matching the roadmap.
  - Only scores whose baseline is old enough to have a full H-day forward
    window contribute to the H-day horizon. This prevents look-ahead: we never
    count a score until its outcome is actually observable.

Sharpe here is the simple ratio of sample mean to sample std of the H-day
returns, annualized by sqrt(365 / H) so different horizons are comparable:

    sharpe = (mean_r / std_r) * sqrt(365 / H)

Win rate = share of scores with forward return > 0. All math is Decimal-based;
None/zero returns are skipped, never treated as 0%.
"""

import math
from datetime import timedelta
from decimal import Decimal

from core.models import Asset, MarketSnapshot, ScoreSnapshot
from core.scoring.tiers import classify_tier, RewardTier

HORIZONS_DAYS = [30, 60, 90]

ZERO = Decimal("0")


def _fwd_snapshot(asset, baseline_observed_at, horizon_days):
    """The MarketSnapshot nearest to baseline+H days that is >= the baseline,
    or None if no such snapshot exists (no forward data available)."""
    target = baseline_observed_at + timedelta(days=horizon_days)
    candidates = (
        MarketSnapshot.objects
        .filter(asset=asset, observed_at__gte=target - timedelta(days=3))
        .order_by("observed_at")
    )
    closest = None
    best_delta = None
    # Allow ±3 days around the target window, preferring the nearest snapshot.
    for snap in candidates:
        if snap.observed_at > target + timedelta(days=3):
            break
        delta = abs((snap.observed_at - target).total_seconds())
        if best_delta is None or delta < best_delta:
            best_delta = delta
            closest = snap
    return closest


def forward_return(asset, baseline_snapshot, horizon_days):
    """Forward % return for a ScoreSnapshot's baseline over `horizon_days`,
    or None when the baseline price or a future price is unavailable."""
    baseline_price = baseline_snapshot.price_usd
    if baseline_price is None or Decimal(str(baseline_price)) == ZERO:
        return None
    future = _fwd_snapshot(asset, baseline_snapshot.observed_at, horizon_days)
    if future is None or future.price_usd is None:
        return None
    future_price = Decimal(str(future.price_usd))
    baseline_dec = Decimal(str(baseline_price))
    if baseline_dec == ZERO:
        return None
    return ((future_price - baseline_dec) / baseline_dec).quantize(Decimal("0.0001"))


def _tier_of(asset, score_10x_snapshot):
    """Reward tier for an asset from its latest per-model scores — the same
    convention the serializers and tier_summary_view use (see
    AssetListSerializer.get_tier). We ignore ScoreSnapshot.computed_at for
    the companion models because it's auto_now_add and not something we can
    pin to a historical point in tests."""
    scores = {}
    conf = {}
    for model in ["10x_potential", "undervaluation", "momentum", "risk"]:
        snap = (
            ScoreSnapshot.objects
            .filter(asset=asset, model_name=model)
            .order_by("-computed_at")
            .first()
        )
        if snap:
            scores[model] = snap.score
            conf[model] = snap.data_confidence

    if not scores:
        return RewardTier.SAFE_2X.value

    result = classify_tier(
        score_10x=scores.get("10x_potential"),
        score_risk=scores.get("risk"),
        score_momentum=scores.get("momentum"),
        score_undervaluation=scores.get("undervaluation"),
        data_confidence_10x=conf.get("10x_potential", ZERO),
        data_confidence_risk=conf.get("risk", ZERO),
        data_confidence_momentum=conf.get("momentum", ZERO),
        data_confidence_undervaluation=conf.get("undervaluation", ZERO),
    )
    return result.tier.value


def _score_10x_of(score_snapshot):
    """The 10X score value for a snapshot (used to bucket by numeric range)."""
    return Decimal(str(score_snapshot.score))


def performance_metrics(returns, horizon_days=30):
    """Aggregate a list of Decimal forward returns into summary metrics.

    Args:
        returns: list of Decimal H-day forward returns.
        horizon_days: the H in "H-day returns", used to annualize Sharpe so
            metrics across different horizons are comparable.

    Returns a dict with win_rate, avg_return, sharpe, count. Empty input yields
    all-None metrics (not fabricated zeros — an empty sample has no measured
    performance).
    """
    if not returns:
        return {"win_rate": None, "avg_return": None, "sharpe": None, "count": 0}

    n = len(returns)
    wins = sum(1 for r in returns if r > ZERO)
    avg = sum(returns, ZERO) / Decimal(str(n))

    mean = avg
    variance_sum = sum((r - mean) ** 2 for r in returns)
    variance = variance_sum / Decimal(str(n))
    std = variance.sqrt() if variance > ZERO else ZERO

    win_rate = (Decimal(str(wins)) / Decimal(str(n))).quantize(Decimal("0.0001"))
    avg_return = avg.quantize(Decimal("0.0001"))

    if std == ZERO:
        sharpe = None
    else:
        # Annualized Sharpe for H-day returns: (mean/std) * sqrt(365 / H).
        annualization = Decimal(str(math.sqrt(365.0 / horizon_days)))
        sharpe = ((avg / std) * annualization).quantize(Decimal("0.0001"))

    return {"win_rate": win_rate, "avg_return": avg_return, "sharpe": sharpe, "count": n}


def build_backtest_report(model_version=None, horizons=None, now=None):
    """Aggregate forward-return performance across all scored assets.

    Groups results per horizon and per reward tier. Optionally narrows to a
    single model_version (recommended — see ScoreSnapshot docstring: scores from
    a newer model shouldn't be compared against an older model's outcomes as if
    they were the same prediction). `now` overrides the reference clock for
    testability.

    Returns:
        {
          "horizons": {
             "30": {"tiers": {tier: metrics}, "summary": {...}},
             ...
          },
          "headline": "...sales pitch string...",
        }
    """
    horizons = horizons or HORIZONS_DAYS
    now = now or _now()
    report = {"horizons": {}}

    qs = (
        ScoreSnapshot.objects
        .filter(model_name="10x_potential")
        .select_related("asset", "market_snapshot")
    )
    if model_version:
        qs = qs.filter(model_version=model_version)

    # One observation per asset per horizon: use the asset's most recent 10X
    # snapshot that has a full forward window. This keeps counts meaningful —
    # we report "N tokens" rather than "N 15-minute re-scored rows", exactly
    # what the sales pitch needs.
    latest_per_asset = {}
    for snap in qs:
        cur = latest_per_asset.get(snap.asset_id)
        if cur is None or (snap.computed_at and (cur.computed_at is None or snap.computed_at > cur.computed_at)):
            latest_per_asset[snap.asset_id] = snap

    for horizon in horizons:
        returns_by_tier = {}
        total_returns = []
        total_scores = 0

        for snap in latest_per_asset.values():
            asset = snap.asset
            baseline = snap.market_snapshot
            if baseline is None:
                continue
            # Only count scores old enough to have a full forward window.
            if baseline.observed_at + timedelta(days=horizon) > now:
                continue
            ret = forward_return(asset, baseline, horizon)
            if ret is None:
                continue
            total_scores += 1
            total_returns.append(ret)
            tier = _tier_of(asset, snap)
            returns_by_tier.setdefault(tier, []).append(ret)

        tiers = {}
        for tier in RewardTier:
            metrics = performance_metrics(returns_by_tier.get(tier.value, []), horizon)
            tiers[tier.value] = metrics

        summary = performance_metrics(total_returns, horizon)
        report["horizons"][str(horizon)] = {
            "tiers": tiers,
            "summary": summary,
            "scores_evaluated": total_scores,
        }

    report["headline"] = build_headline(report, horizons, now)
    return report


def _now():
    from django.utils import timezone
    return timezone.now()


def build_headline(report, horizons=HORIZONS_DAYS, now=None):
    """A public-facing, saleable one-liner, e.g.:

        "Tokens scored 80+ returned avg +12.3% in 90 days (win rate 71%)."

    Falls back gracefully when there's no data yet.
    """
    horizon = max(horizons)  # prefer the longest, most reliable horizon
    summary = report["horizons"].get(str(horizon), {}).get("summary", {})
    count = summary.get("count", 0)
    if not count:
        return (
            f"Not enough historical data yet. Accuracy stats will appear once "
            f"tokens have enough forward price history ({horizon} days) to measure."
        )
    avg = summary.get("avg_return")
    wr = summary.get("win_rate")
    if avg is None or wr is None:
        return "Not enough historical data to compute accuracy stats."
    return (
        f"Over the last {horizon} days, scored tokens returned on average "
        f"{avg * Decimal('100'):.1f}% with a {wr * Decimal('100'):.0f}% win rate "
        f"across {count} score observations."
    )
