import logging

from celery import shared_task

from core.models import Asset, ScoreSnapshot
from core.scoring import momentum, potential_10x, risk, undervaluation
from core.scoring.persistence import save_score_result
from core.scoring.tiers import classify_tier, RewardTier

logger = logging.getLogger(__name__)

# Order matters here, deliberately: momentum is scored for EVERY asset in
# its own first pass, before any of the other scorers run for ANY asset.
#
# This is required, not stylistic. 10X Potential's narrative_sector
# factor (Phase 8) reads OTHER assets' latest momentum ScoreSnapshot to
# compute a sector median. A single interleaved pass — score one asset's
# all 4 models, then move to the next asset — makes narrative_sector's
# result depend on iteration order: an asset scored early in the batch
# sees none of its peers' momentum data yet, even though those peers
# WOULD have momentum data by the time the batch finished. This was
# caught during final review (see PHASE_9_NOTES.md) by running a full,
# multi-asset regression scenario rather than trusting per-chunk smoke
# tests in isolation — those never exercised more than 1-2 assets sharing
# a sector in the same batch run, so the bug never surfaced until now.
#
# Momentum itself never depends on any OTHER asset's score, so it's
# always safe to run first across the whole batch.
OTHER_SCORERS = [
    (potential_10x, "compute_10x_potential_score"),
    (undervaluation, "compute_undervaluation_score"),
    (risk, "compute_risk_score"),
]


@shared_task
def score_all_assets():
    """Compute and persist all 4 scores for every active asset's latest
    MarketSnapshot. Assets with no snapshot yet are skipped (nothing to
    score) rather than erroring the whole batch. See OTHER_SCORERS'
    comment above for why momentum runs in its own first pass."""

    assets_with_snapshots = []
    skipped = 0
    for asset in Asset.objects.filter(is_active=True):
        latest = asset.market_snapshots.order_by("-observed_at").first()
        if latest is None:
            skipped += 1
            continue
        assets_with_snapshots.append((asset, latest))

    errored = 0

    # Pass 1: momentum for every asset, so any cross-asset aggregate
    # factor computed in pass 2 (narrative_sector) sees the FULL batch's
    # momentum data — not just whichever assets happened to be reached
    # earlier in iteration order.
    for asset, latest in assets_with_snapshots:
        try:
            result = momentum.compute_momentum_score(asset, latest)
            save_score_result(asset, latest, result)
        except Exception:
            logger.exception("Scoring failed for asset=%s scorer=compute_momentum_score", asset.symbol)
            errored += 1

    # Pass 2: everything else, now free to rely on the full batch's
    # momentum data already being present.
    scored = 0
    for asset, latest in assets_with_snapshots:
        for module, attr_name in OTHER_SCORERS:
            try:
                scorer = getattr(module, attr_name)
                result = scorer(asset, latest)
                save_score_result(asset, latest, result)
            except Exception:
                # One scorer failing on one asset should never block the
                # rest of the batch — log and continue, per section 40
                # (failure handling should degrade gracefully, not
                # cascade).
                logger.exception("Scoring failed for asset=%s scorer=%s", asset.symbol, attr_name)
                errored += 1
                continue
        scored += 1

    return {"scored": scored, "skipped_no_snapshot": skipped, "scorer_errors": errored}


def compute_asset_tier(asset):
    """Compute the reward tier for an asset based on its latest scores.

    Called after all 4 scores are persisted. Looks up the most recent
    ScoreSnapshot for each model and feeds them into the tier classifier.
    """
    latest = {}
    for model_name in ["10x_potential", "undervaluation", "momentum", "risk"]:
        snap = (
            ScoreSnapshot.objects
            .filter(asset=asset, model_name=model_name)
            .order_by("-computed_at")
            .first()
        )
        if snap:
            latest[model_name] = snap

    if not latest:
        return None

    tier_result = classify_tier(
        score_10x=latest.get("10x_potential", ScoreSnapshot()).score if "10x_potential" in latest else None,
        score_risk=latest.get("risk", ScoreSnapshot()).score if "risk" in latest else None,
        score_momentum=latest.get("momentum", ScoreSnapshot()).score if "momentum" in latest else None,
        score_undervaluation=latest.get("undervaluation", ScoreSnapshot()).score if "undervaluation" in latest else None,
        data_confidence_10x=latest.get("10x_potential", ScoreSnapshot()).data_confidence if "10x_potential" in latest else Decimal("0"),
        data_confidence_risk=latest.get("risk", ScoreSnapshot()).data_confidence if "risk" in latest else Decimal("0"),
        data_confidence_momentum=latest.get("momentum", ScoreSnapshot()).data_confidence if "momentum" in latest else Decimal("0"),
        data_confidence_undervaluation=latest.get("undervaluation", ScoreSnapshot()).data_confidence if "undervaluation" in latest else Decimal("0"),
    )
    return tier_result
