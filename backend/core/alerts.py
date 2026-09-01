"""
Alert rule evaluation (Roadmap Tier 1, Feature 2).

Pure, DB-independent logic: given a rule and the latest observed metrics
for an asset, decide whether the rule fires and produce the human-readable
details. Deliberately free of I/O so it is easy to unit test; the Celery
task (core.tasks.alerts) feeds it current snapshot data and dispatches
deliveries.
"""

from decimal import Decimal

from core.models import AlertRule, ScoreSnapshot


METRIC_LABELS = {
    AlertRule.Metric.SCORE_10X: "10X Potential score",
    AlertRule.Metric.SCORE_UNDERVALUATION: "Undervaluation score",
    AlertRule.Metric.SCORE_MOMENTUM: "Momentum score",
    AlertRule.Metric.SCORE_RISK: "Risk score",
    AlertRule.Metric.MARKET_CAP_PCT_CHANGE_24H: "Market cap 24h change",
    AlertRule.Metric.VOLUME_PCT_CHANGE_24H: "Volume 24h change",
}

OPERATOR_LABELS = {
    AlertRule.Operator.GT: ">",
    AlertRule.Operator.GTE: ">=",
    AlertRule.Operator.LT: "<",
    AlertRule.Operator.LTE: "<=",
}


def metric_label(metric: str) -> str:
    return METRIC_LABELS.get(metric, metric)


def operator_label(operator: str) -> str:
    return OPERATOR_LABELS.get(operator, operator)


def get_latest_score(asset, model_name) -> ScoreSnapshot | None:
    return (
        ScoreSnapshot.objects
        .filter(asset=asset, model_name=model_name)
        .order_by("-computed_at")
        .first()
    )


def get_metric_value(asset, metric):
    """Return the current numeric value for a metric of an asset, or None
    if there isn't enough data yet. Values are returned as Decimal for
    threshold comparison; None means 'cannot evaluate this cycle'."""
    from core.models import MarketSnapshot

    # Score metrics read the latest ScoreSnapshot for the matching model.
    score_metric_map = {
        AlertRule.Metric.SCORE_10X: "10x_potential",
        AlertRule.Metric.SCORE_UNDERVALUATION: "undervaluation",
        AlertRule.Metric.SCORE_MOMENTUM: "momentum",
        AlertRule.Metric.SCORE_RISK: "risk",
    }
    if metric in score_metric_map:
        snap = get_latest_score(asset, score_metric_map[metric])
        return Decimal(snap.score) if snap else None

    # Market metrics read the latest MarketSnapshot + the one before it to
    # compute a % change over 24h. If there's only one snapshot (or prices
    # are missing), there's nothing to compare against — skip this cycle.
    market_snaps = list(
        MarketSnapshot.objects.filter(asset=asset).order_by("-observed_at")[:2]
    )
    if len(market_snaps) < 2:
        return None

    latest, previous = market_snaps[0], market_snaps[1]
    if metric == AlertRule.Metric.MARKET_CAP_PCT_CHANGE_24H:
        return _pct_change(latest.market_cap_usd, previous.market_cap_usd)
    if metric == AlertRule.Metric.VOLUME_PCT_CHANGE_24H:
        return _pct_change(latest.volume_24h_usd, previous.volume_24h_usd)
    return None


def _pct_change(current, previous):
    if (
        current is None or previous is None
        or Decimal(str(previous)) == 0
    ):
        return None
    return (
        (Decimal(str(current)) - Decimal(str(previous)))
        / Decimal(str(previous))
        * Decimal("100")
    ).quantize(Decimal("0.0001"))


def threshold_crossed(operator: str, value: Decimal, threshold: Decimal) -> bool:
    ops = {
        AlertRule.Operator.GT: lambda v, t: v > t,
        AlertRule.Operator.GTE: lambda v, t: v >= t,
        AlertRule.Operator.LT: lambda v, t: v < t,
        AlertRule.Operator.LTE: lambda v, t: v <= t,
    }
    func = ops.get(operator)
    if func is None:
        return False
    return bool(func(Decimal(value), Decimal(threshold)))
