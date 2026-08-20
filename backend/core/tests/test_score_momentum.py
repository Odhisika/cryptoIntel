from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.models import Asset, MarketSnapshot
from core.scoring.momentum import compute_momentum_score

pytestmark = pytest.mark.django_db


def make_asset():
    return Asset.objects.create(symbol="tst", name="Test Coin", external_ids={"coingecko": "test-coin"})


def snap(asset, price, days_ago, volume=Decimal("1000000")):
    return MarketSnapshot.objects.create(
        asset=asset,
        price_usd=price,
        market_cap_usd=Decimal("100000000"),
        volume_24h_usd=volume,
        source="coingecko",
        observed_at=datetime(2026, 2, 1, tzinfo=timezone.utc) - timedelta(days=days_ago),
    )


def test_no_history_marks_all_return_windows_insufficient():
    asset = make_asset()
    current = snap(asset, Decimal("1"), days_ago=0)
    result = compute_momentum_score(asset, current)

    missing_names = {f.name for f in result.missing_factors()}
    assert {"return_7d", "return_30d", "return_90d"}.issubset(missing_names)
    assert result.data_confidence == Decimal("0")


def test_positive_return_scores_above_neutral():
    asset = make_asset()
    snap(asset, Decimal("1"), days_ago=7)
    current = snap(asset, Decimal("1.5"), days_ago=0)  # +50% over 7D

    result = compute_momentum_score(asset, current)
    return_7d = next(f for f in result.factors if f.name == "return_7d")
    assert return_7d.insufficient_data is False
    assert return_7d.normalized_value > Decimal("50")


def test_negative_return_scores_below_neutral():
    asset = make_asset()
    snap(asset, Decimal("2"), days_ago=7)
    current = snap(asset, Decimal("1"), days_ago=0)  # -50% over 7D

    result = compute_momentum_score(asset, current)
    return_7d = next(f for f in result.factors if f.name == "return_7d")
    assert return_7d.normalized_value < Decimal("50")


def test_relative_strength_always_insufficient_in_v1():
    asset = make_asset()
    current = snap(asset, Decimal("1"), days_ago=0)
    result = compute_momentum_score(asset, current)
    rel_strength = next(f for f in result.factors if f.name == "relative_strength")
    assert rel_strength.insufficient_data is True


def test_stale_baseline_outside_tolerance_is_insufficient():
    asset = make_asset()
    # Snapshot 20 days before the 7D mark -> nowhere near tolerance for a
    # 7D-back lookup.
    snap(asset, Decimal("1"), days_ago=27)
    current = snap(asset, Decimal("2"), days_ago=0)

    result = compute_momentum_score(asset, current)
    return_7d = next(f for f in result.factors if f.name == "return_7d")
    assert return_7d.insufficient_data is True
