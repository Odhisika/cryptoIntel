from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.models import Asset, MarketSnapshot
from core.scoring.potential_10x import compute_10x_potential_score

pytestmark = pytest.mark.django_db


def make_asset_with_snapshot(**snap_kwargs):
    asset = Asset.objects.create(symbol="tst", name="Test Coin", external_ids={"coingecko": "test-coin"})
    defaults = dict(
        asset=asset,
        price_usd=Decimal("1"),
        market_cap_usd=Decimal("100000000"),
        fully_diluted_valuation_usd=Decimal("100000000"),
        volume_24h_usd=Decimal("5000000"),
        source="coingecko",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    defaults.update(snap_kwargs)
    snapshot = MarketSnapshot.objects.create(**defaults)
    return asset, snapshot


def test_score_is_within_0_100():
    asset, snapshot = make_asset_with_snapshot()
    result = compute_10x_potential_score(asset, snapshot)
    assert Decimal("0") <= result.score <= Decimal("100")


def test_data_confidence_reflects_available_factors_only():
    # Only 3 of 10 factors (market_cap_opportunity, valuation, liquidity =
    # 15+10+10=35) are ever computable at Phase 2; confidence should
    # reflect that, never claim more.
    asset, snapshot = make_asset_with_snapshot()
    result = compute_10x_potential_score(asset, snapshot)
    assert result.data_confidence == Decimal("0.3500")


def test_smaller_market_cap_scores_higher_on_opportunity_factor():
    small_asset, small_snap = make_asset_with_snapshot(market_cap_usd=Decimal("20000000"))
    large_asset, large_snap = make_asset_with_snapshot(market_cap_usd=Decimal("1800000000"))

    small_result = compute_10x_potential_score(small_asset, small_snap)
    large_result = compute_10x_potential_score(large_asset, large_snap)

    small_factor = next(f for f in small_result.factors if f.name == "market_cap_opportunity")
    large_factor = next(f for f in large_result.factors if f.name == "market_cap_opportunity")
    assert small_factor.normalized_value > large_factor.normalized_value


def test_heavy_dilution_overhang_lowers_valuation_factor():
    low_dilution_asset, low_dilution_snap = make_asset_with_snapshot(
        market_cap_usd=Decimal("100000000"), fully_diluted_valuation_usd=Decimal("100000000")
    )
    heavy_dilution_asset, heavy_dilution_snap = make_asset_with_snapshot(
        market_cap_usd=Decimal("100000000"), fully_diluted_valuation_usd=Decimal("1000000000")
    )

    low_result = compute_10x_potential_score(low_dilution_asset, low_dilution_snap)
    heavy_result = compute_10x_potential_score(heavy_dilution_asset, heavy_dilution_snap)

    low_val = next(f for f in low_result.factors if f.name == "valuation").normalized_value
    heavy_val = next(f for f in heavy_result.factors if f.name == "valuation").normalized_value
    assert low_val > heavy_val


def test_missing_fdv_marks_valuation_insufficient():
    asset, snapshot = make_asset_with_snapshot(fully_diluted_valuation_usd=None)
    result = compute_10x_potential_score(asset, snapshot)
    valuation_factor = next(f for f in result.factors if f.name == "valuation")
    assert valuation_factor.insufficient_data is True


def test_unimplemented_factors_are_marked_insufficient():
    asset, snapshot = make_asset_with_snapshot()
    result = compute_10x_potential_score(asset, snapshot)
    missing_names = {f.name for f in result.missing_factors()}
    assert missing_names == {
        "fundamentals", "growth", "tokenomics", "narrative_sector",
        "developer_activity", "onchain_adoption", "catalysts", "dex_activity",
    }


def test_mismatched_snapshot_raises():
    asset, _ = make_asset_with_snapshot()
    _, other_snapshot = make_asset_with_snapshot()
    with pytest.raises(ValueError):
        compute_10x_potential_score(asset, other_snapshot)
