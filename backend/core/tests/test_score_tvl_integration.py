from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.models import Asset, MarketSnapshot, Protocol, TVLSnapshot
from core.scoring.potential_10x import compute_10x_potential_score
from core.scoring.undervaluation import compute_undervaluation_score

pytestmark = pytest.mark.django_db


def make_asset_with_snapshot(market_cap=Decimal("100000000")):
    asset = Asset.objects.create(symbol="uni", name="Uniswap", external_ids={"coingecko": "uniswap"})
    snapshot = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("5"), market_cap_usd=market_cap,
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return asset, snapshot


def make_tvl_snapshot(asset, tvl_usd, change_7d_pct=None):
    protocol = Protocol.objects.create(asset=asset, slug="uniswap", name="Uniswap")
    return TVLSnapshot.objects.create(
        protocol=protocol, tvl_usd=tvl_usd, change_7d_pct=change_7d_pct,
        source="defillama", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_undervaluation_tvl_factor_is_insufficient_without_matched_protocol():
    asset, snapshot = make_asset_with_snapshot()
    result = compute_undervaluation_score(asset, snapshot)
    tvl_factor = next(f for f in result.factors if f.name == "tvl_to_market_cap")
    assert tvl_factor.insufficient_data is True


def test_undervaluation_tvl_factor_computed_with_matched_protocol():
    asset, snapshot = make_asset_with_snapshot(market_cap=Decimal("100000000"))
    make_tvl_snapshot(asset, tvl_usd=Decimal("200000000"))  # 2x MC == healthy threshold

    result = compute_undervaluation_score(asset, snapshot)
    tvl_factor = next(f for f in result.factors if f.name == "tvl_to_market_cap")
    assert tvl_factor.insufficient_data is False
    assert tvl_factor.normalized_value == Decimal("100")


def test_undervaluation_confidence_increases_with_tvl_data():
    asset_no_tvl, snap_no_tvl = make_asset_with_snapshot()
    asset_with_tvl, snap_with_tvl = make_asset_with_snapshot()
    make_tvl_snapshot(asset_with_tvl, tvl_usd=Decimal("50000000"), change_7d_pct=Decimal("10"))

    result_no_tvl = compute_undervaluation_score(asset_no_tvl, snap_no_tvl)
    result_with_tvl = compute_undervaluation_score(asset_with_tvl, snap_with_tvl)

    assert result_with_tvl.data_confidence > result_no_tvl.data_confidence
    assert result_no_tvl.data_confidence == Decimal("0")


def test_undervaluation_growth_factor_uses_tvl_change():
    asset, snapshot = make_asset_with_snapshot()
    make_tvl_snapshot(asset, tvl_usd=Decimal("50000000"), change_7d_pct=Decimal("20"))

    result = compute_undervaluation_score(asset, snapshot)
    growth = next(f for f in result.factors if f.name == "fundamentals_growth")
    assert growth.insufficient_data is False
    assert growth.normalized_value == Decimal("100")  # capped at +20%


def test_10x_potential_fundamentals_factor_computed_from_tvl():
    asset, snapshot = make_asset_with_snapshot(market_cap=Decimal("100000000"))
    make_tvl_snapshot(asset, tvl_usd=Decimal("200000000"))

    result = compute_10x_potential_score(asset, snapshot)
    fundamentals = next(f for f in result.factors if f.name == "fundamentals")
    assert fundamentals.insufficient_data is False
    assert fundamentals.normalized_value == Decimal("100")


def test_10x_potential_fundamentals_insufficient_for_non_defi_asset():
    asset, snapshot = make_asset_with_snapshot()
    result = compute_10x_potential_score(asset, snapshot)
    fundamentals = next(f for f in result.factors if f.name == "fundamentals")
    assert fundamentals.insufficient_data is True
