"""
Shared snapshot-lookup helper. Finding "the snapshot from ~N days ago" is
needed by both Momentum Score (price/volume returns) and the tokenomics
math module (supply inflation) — extracted here rather than duplicated,
after the second use made the duplication worth removing.
"""

from datetime import timedelta
from typing import Optional

from core.models import Asset, MarketSnapshot

# How far off from the exact N-day mark a snapshot can be and still count
# as "the N-day baseline." Generous enough to tolerate ingestion gaps
# without silently comparing against a snapshot that's actually days off
# from where the label claims.
DEFAULT_LOOKUP_TOLERANCE = timedelta(hours=18)


def find_baseline_snapshot(
    asset: Asset, as_of, days_back: int, *, tolerance: timedelta = DEFAULT_LOOKUP_TOLERANCE
) -> Optional[MarketSnapshot]:
    target_time = as_of - timedelta(days=days_back)
    candidate = (
        MarketSnapshot.objects.filter(asset=asset, observed_at__lte=target_time)
        .order_by("-observed_at")
        .first()
    )
    if candidate is None:
        return None
    if (target_time - candidate.observed_at) > tolerance:
        return None
    return candidate
