"""
Narrative engine (section 24) — the subset buildable with zero new
providers.

Section 24's full ask monitors sector performance, volume, social
attention, search interest, developer activity, funding, protocol
launches, news, and ecosystem growth. Of those, this codebase has real
data for exactly two: sector performance (via Momentum Score, Phase 2)
and developer activity (via commit activity, Phase 6). Social attention,
search interest, funding, and news all require providers Phase 8's
research found to be paid-only or nonexistent for free (see
docs/DATA_LICENSING.md) — same standing gap as social sentiment (section
25) and catalysts (section 23).

What this computes: for a given sector, the median Momentum Score and
median recent commit activity across all assets currently classified
into that sector, using each asset's MOST RECENT ScoreSnapshot/
DeveloperActivitySnapshot — same data already computed by Phase 2/6, just
aggregated by sector rather than re-derived from raw data. No new model,
no persistence — computed on demand, same precedent as
core.scoring.comparables (Phase 3.3), for the same reason: this is
derived analytics over already-trusted data, not a new source of truth
that needs its own audit trail.

Labeled explicitly as a PARTIAL, price/momentum-driven proxy for
"narrative strength" — not the richer social/search/funding-driven signal
section 24 actually describes. Any factor or UI surface that uses this
must carry that caveat forward, not present it as a full narrative
detection system.
"""

import statistics
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from core.models import Asset, DeveloperActivitySnapshot, ScoreSnapshot


@dataclass(frozen=True)
class SectorNarrativeSnapshot:
    sector: str
    asset_count: int
    median_momentum_score: Optional[Decimal]
    median_commits_4w: Optional[Decimal]


def _latest_momentum_scores(sector: str) -> list[Decimal]:
    """Only considers momentum snapshots with data_confidence > 0.

    A momentum score of 0 with 0% confidence means "no price history
    exists yet" — a brand-new asset's placeholder result, not a real
    signal that the asset's price is flat. Including those would corrupt
    the sector median with fake "confirmed zero momentum" data points.
    This distinction was caught during final review (see PHASE_9_NOTES.md)
    by running a multi-asset batch scenario where several sector peers
    genuinely had no price history yet — exactly the case this filter
    exists to handle correctly."""
    asset_ids = Asset.objects.filter(sector=sector, is_active=True).values_list("id", flat=True)
    scores = []
    for asset_id in asset_ids:
        latest = (
            ScoreSnapshot.objects.filter(asset_id=asset_id, model_name="momentum", data_confidence__gt=0)
            .order_by("-computed_at")
            .first()
        )
        if latest is not None:
            scores.append(latest.score)
    return scores


def _latest_commit_activity(sector: str) -> list[int]:
    asset_ids = Asset.objects.filter(sector=sector, is_active=True).values_list("id", flat=True)
    counts = []
    for asset_id in asset_ids:
        latest = (
            DeveloperActivitySnapshot.objects.filter(asset_id=asset_id, commits_4w__isnull=False)
            .order_by("-observed_at")
            .first()
        )
        if latest is not None:
            counts.append(latest.commits_4w)
    return counts


def compute_sector_narrative(sector: str) -> SectorNarrativeSnapshot:
    asset_count = Asset.objects.filter(sector=sector, is_active=True).count()

    momentum_scores = _latest_momentum_scores(sector)
    median_momentum = Decimal(str(statistics.median(momentum_scores))) if momentum_scores else None

    commit_counts = _latest_commit_activity(sector)
    median_commits = Decimal(str(statistics.median(commit_counts))) if commit_counts else None

    return SectorNarrativeSnapshot(
        sector=sector,
        asset_count=asset_count,
        median_momentum_score=median_momentum,
        median_commits_4w=median_commits,
    )


def rank_sectors_by_momentum(min_assets: int = 3) -> list[SectorNarrativeSnapshot]:
    """Sectors with at least `min_assets` classified assets, ranked by
    median momentum descending — a coarse "which narratives are hot right
    now" view. Sectors below the asset-count threshold are excluded
    entirely rather than shown with a misleadingly confident single-asset
    median."""
    snapshots = []
    for sector, _ in Asset.Sector.choices:
        snapshot = compute_sector_narrative(sector)
        if snapshot.asset_count >= min_assets and snapshot.median_momentum_score is not None:
            snapshots.append(snapshot)
    return sorted(snapshots, key=lambda s: s.median_momentum_score, reverse=True)
