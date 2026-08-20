"""
Ranking engine (section 33).

Deliberately simple for v1: rank by latest score per (asset, model_name,
model_version) — the "same model version" constraint matters, since
ranking a v1.0 score next to a v1.1 score would silently mix
methodologies. Historical ranking snapshots (a `RankingSnapshot` model)
are a Phase 2 follow-up once this is proven useful; for now rankings are
computed on demand from ScoreSnapshot, which is itself already historical.
"""

from dataclasses import dataclass
from decimal import Decimal
from functools import reduce
from operator import or_

from django.db.models import Max, Q

from core.models import ScoreSnapshot


@dataclass(frozen=True)
class RankedAsset:
    rank: int
    asset_id: str
    symbol: str
    score: Decimal
    data_confidence: Decimal


def rank_by_model(
    model_name: str, model_version: str, *, min_data_confidence: Decimal = Decimal("0"), limit: int = 50
) -> list[RankedAsset]:
    """Rank assets by their most recent score for the given model+version.

    min_data_confidence lets callers exclude scores backed by too little
    real data from a public ranking — e.g. the public "Potential 10X" page
    (Phase 11) should probably not surface a rank built on a
    data_confidence of 0.05, even though the number technically exists.
    """

    # Portable across Postgres and SQLite (used in tests) — avoids
    # Postgres-only `distinct(field)` (DISTINCT ON) in favor of a
    # group-by-max-timestamp-then-match approach.
    latest_per_asset = (
        ScoreSnapshot.objects.filter(model_name=model_name, model_version=model_version)
        .values("asset_id")
        .annotate(latest=Max("computed_at"))
    )

    if not latest_per_asset:
        return []

    match_query = reduce(
        or_, (Q(asset_id=row["asset_id"], computed_at=row["latest"]) for row in latest_per_asset)
    )

    qs = (
        ScoreSnapshot.objects.filter(match_query, model_name=model_name, model_version=model_version)
        .filter(data_confidence__gte=min_data_confidence)
        .select_related("asset")
        .order_by("-score")[:limit]
    )

    return [
        RankedAsset(
            rank=i + 1,
            asset_id=str(s.asset_id),
            symbol=s.asset.symbol,
            score=s.score,
            data_confidence=s.data_confidence,
        )
        for i, s in enumerate(qs)
    ]
