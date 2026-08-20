from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.models import Asset, DeveloperActivitySnapshot, MarketSnapshot, ScoreSnapshot
from core.scoring.narrative import compute_sector_narrative, rank_sectors_by_momentum

pytestmark = pytest.mark.django_db


def make_asset_with_momentum(symbol, sector, momentum_score):
    asset = Asset.objects.create(symbol=symbol, name=symbol.upper(), sector=sector)
    snap = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    ScoreSnapshot.objects.create(
        asset=asset, market_snapshot=snap, model_name="momentum", model_version="v1.0",
        score=momentum_score, data_confidence=Decimal("1.0"),
    )
    return asset


def test_no_assets_in_sector_returns_none_median():
    result = compute_sector_narrative(Asset.Sector.GAMING)
    assert result.asset_count == 0
    assert result.median_momentum_score is None


def test_median_momentum_computed_across_sector():
    make_asset_with_momentum("a", Asset.Sector.L1, Decimal("40"))
    make_asset_with_momentum("b", Asset.Sector.L1, Decimal("60"))
    make_asset_with_momentum("c", Asset.Sector.L1, Decimal("80"))

    result = compute_sector_narrative(Asset.Sector.L1)
    assert result.asset_count == 3
    assert result.median_momentum_score == Decimal("60")


def test_different_sector_assets_excluded():
    make_asset_with_momentum("a", Asset.Sector.L1, Decimal("40"))
    make_asset_with_momentum("b", Asset.Sector.MEME, Decimal("90"))

    result = compute_sector_narrative(Asset.Sector.L1)
    assert result.asset_count == 1
    assert result.median_momentum_score == Decimal("40")


def test_uses_most_recent_score_snapshot_only():
    asset = make_asset_with_momentum("a", Asset.Sector.L1, Decimal("40"))
    # A second, more recent momentum snapshot should win.
    snap2 = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        source="coingecko", observed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    ScoreSnapshot.objects.create(
        asset=asset, market_snapshot=snap2, model_name="momentum", model_version="v1.0",
        score=Decimal("95"), data_confidence=Decimal("1.0"),
    )
    result = compute_sector_narrative(Asset.Sector.L1)
    assert result.median_momentum_score == Decimal("95")


def test_zero_confidence_momentum_snapshots_are_excluded_from_median():
    """Regression test for the bug caught during final review: a brand-
    new asset's momentum score is 0 with 0% confidence (no price history
    yet) — that's fundamentally different from a real "confirmed flat
    price" 0, and including it would corrupt the sector median. Three
    real, confident momentum scores plus one zero-confidence placeholder
    should median to the real scores' median, ignoring the placeholder
    entirely."""
    make_asset_with_momentum("a", Asset.Sector.L1, Decimal("40"))
    make_asset_with_momentum("b", Asset.Sector.L1, Decimal("60"))
    make_asset_with_momentum("c", Asset.Sector.L1, Decimal("80"))

    # A 4th asset with a momentum ScoreSnapshot that's a real "0, no data"
    # placeholder (0% confidence) rather than a genuine flat-price result.
    new_asset = Asset.objects.create(symbol="brandnew", name="BrandNew", sector=Asset.Sector.L1)
    new_snap = MarketSnapshot.objects.create(
        asset=new_asset, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    ScoreSnapshot.objects.create(
        asset=new_asset, market_snapshot=new_snap, model_name="momentum", model_version="v1.0",
        score=Decimal("0"), data_confidence=Decimal("0"),
    )

    result = compute_sector_narrative(Asset.Sector.L1)
    # Median of [40, 60, 80] = 60, NOT median of [0, 40, 60, 80] = 50.
    assert result.median_momentum_score == Decimal("60")


def test_commit_activity_median_computed():
    asset = make_asset_with_momentum("a", Asset.Sector.L1, Decimal("40"))
    DeveloperActivitySnapshot.objects.create(
        asset=asset, stars=1, forks=1, open_issues=1, commits_4w=50,
        source="github", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    result = compute_sector_narrative(Asset.Sector.L1)
    assert result.median_commits_4w == Decimal("50")


def test_rank_sectors_excludes_below_min_assets_threshold():
    make_asset_with_momentum("a", Asset.Sector.L1, Decimal("40"))
    make_asset_with_momentum("b", Asset.Sector.L1, Decimal("60"))
    # Only 2 assets in L1 — below the default min_assets=3 threshold.
    ranked = rank_sectors_by_momentum()
    assert Asset.Sector.L1 not in [s.sector for s in ranked]


def test_rank_sectors_includes_sectors_meeting_threshold():
    make_asset_with_momentum("a", Asset.Sector.L1, Decimal("40"))
    make_asset_with_momentum("b", Asset.Sector.L1, Decimal("60"))
    make_asset_with_momentum("c", Asset.Sector.L1, Decimal("80"))

    ranked = rank_sectors_by_momentum()
    assert Asset.Sector.L1 in [s.sector for s in ranked]


def test_rank_sectors_orders_by_momentum_descending():
    for i in range(3):
        make_asset_with_momentum(f"l1_{i}", Asset.Sector.L1, Decimal("30"))
    for i in range(3):
        make_asset_with_momentum(f"meme_{i}", Asset.Sector.MEME, Decimal("90"))

    ranked = rank_sectors_by_momentum()
    assert ranked[0].sector == Asset.Sector.MEME
