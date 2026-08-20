from django.db import transaction

from core.models import Asset, MarketSnapshot, ScoreFactor, ScoreSnapshot
from core.scoring.base import ScoreResult


@transaction.atomic
def save_score_result(asset: Asset, market_snapshot: MarketSnapshot, result: ScoreResult) -> ScoreSnapshot:
    snapshot, created = ScoreSnapshot.objects.update_or_create(
        asset=asset,
        model_name=result.model_name,
        model_version=result.model_version,
        market_snapshot=market_snapshot,
        defaults={"score": result.score, "data_confidence": result.data_confidence},
    )

    if not created:
        snapshot.score_factors.all().delete()

    ScoreFactor.objects.bulk_create(
        [
            ScoreFactor(
                score_snapshot=snapshot,
                name=f.name,
                weight=f.weight,
                normalized_value=f.normalized_value,
                raw_value=f.raw_value,
                insufficient_data=f.insufficient_data,
                note=f.note,
            )
            for f in result.factors
        ]
    )

    return snapshot
