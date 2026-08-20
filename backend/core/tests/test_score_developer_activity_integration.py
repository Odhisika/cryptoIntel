from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.models import Asset, DeveloperActivitySnapshot, MarketSnapshot
from core.scoring.potential_10x import compute_10x_potential_score

pytestmark = pytest.mark.django_db


def make_asset_with_snapshot():
    asset = Asset.objects.create(symbol="tst", name="Test Coin", external_ids={"coingecko": "test"})
    snapshot = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return asset, snapshot


def make_dev_snapshot(asset, commits_4w=None, is_archived=False):
    return DeveloperActivitySnapshot.objects.create(
        asset=asset, stars=100, forks=10, open_issues=5, is_archived=is_archived,
        commits_4w=commits_4w, source="github", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_insufficient_without_dev_activity_data():
    asset, snapshot = make_asset_with_snapshot()
    result = compute_10x_potential_score(asset, snapshot)
    dev = next(f for f in result.factors if f.name == "developer_activity")
    assert dev.insufficient_data is True


def test_archived_repo_scores_zero():
    asset, snapshot = make_asset_with_snapshot()
    make_dev_snapshot(asset, commits_4w=50, is_archived=True)
    result = compute_10x_potential_score(asset, snapshot)
    dev = next(f for f in result.factors if f.name == "developer_activity")
    assert dev.insufficient_data is False
    assert dev.normalized_value == Decimal("0")


def test_high_commit_activity_scores_high():
    asset, snapshot = make_asset_with_snapshot()
    make_dev_snapshot(asset, commits_4w=100)
    result = compute_10x_potential_score(asset, snapshot)
    dev = next(f for f in result.factors if f.name == "developer_activity")
    assert dev.normalized_value == Decimal("100")


def test_zero_commits_scores_zero_but_is_not_insufficient():
    asset, snapshot = make_asset_with_snapshot()
    make_dev_snapshot(asset, commits_4w=0)
    result = compute_10x_potential_score(asset, snapshot)
    dev = next(f for f in result.factors if f.name == "developer_activity")
    assert dev.insufficient_data is False
    assert dev.normalized_value == Decimal("0")


def test_missing_commit_stats_is_insufficient_not_zero():
    # commits_4w=None (202 from GitHub) should be a DIFFERENT outcome from
    # commits_4w=0 (confirmed zero commits) — the whole point of keeping
    # this nullable rather than defaulting to 0.
    asset, snapshot = make_asset_with_snapshot()
    make_dev_snapshot(asset, commits_4w=None)
    result = compute_10x_potential_score(asset, snapshot)
    dev = next(f for f in result.factors if f.name == "developer_activity")
    assert dev.insufficient_data is True


def test_partial_commit_activity_scales_linearly():
    asset, snapshot = make_asset_with_snapshot()
    make_dev_snapshot(asset, commits_4w=50)
    result = compute_10x_potential_score(asset, snapshot)
    dev = next(f for f in result.factors if f.name == "developer_activity")
    assert dev.normalized_value == Decimal("50")
