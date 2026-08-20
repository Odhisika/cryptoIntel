from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.models import Asset, FeeSnapshot, MarketSnapshot, Protocol, RevenueSnapshot
from core.scoring.undervaluation import compute_undervaluation_score

pytestmark = pytest.mark.django_db


def make_asset_with_snapshot(market_cap=Decimal("100000000"), fdv=None):
    asset = Asset.objects.create(symbol="uni", name="Uniswap", external_ids={"coingecko": "uniswap"})
    snapshot = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("5"), market_cap_usd=market_cap,
        fully_diluted_valuation_usd=fdv,
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return asset, snapshot


def make_protocol(asset):
    return Protocol.objects.create(asset=asset, slug="uniswap", name="Uniswap")


def test_fees_factor_insufficient_without_fee_snapshot():
    asset, snapshot = make_asset_with_snapshot()
    result = compute_undervaluation_score(asset, snapshot)
    fees = next(f for f in result.factors if f.name == "fees_to_market_cap")
    assert fees.insufficient_data is True


def test_fees_factor_computed_from_annualized_30d_total():
    asset, snapshot = make_asset_with_snapshot(market_cap=Decimal("100000000"))
    protocol = make_protocol(asset)
    # 30D total of 2.5M -> annualized (x12) = 30M -> ratio to 100M MC = 30%,
    # healthy threshold is also 30%, so this should hit the 100 cap exactly.
    FeeSnapshot.objects.create(
        protocol=protocol, fees_24h_usd=Decimal("100000"), fees_30d_usd=Decimal("2500000"),
        source="defillama", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    result = compute_undervaluation_score(asset, snapshot)
    fees = next(f for f in result.factors if f.name == "fees_to_market_cap")
    assert fees.insufficient_data is False
    assert fees.normalized_value == Decimal("100")


def test_revenue_factors_insufficient_when_protocol_takes_no_cut():
    asset, snapshot = make_asset_with_snapshot()
    make_protocol(asset)  # protocol exists but has no RevenueSnapshot at all
    result = compute_undervaluation_score(asset, snapshot)
    for name in ["revenue_to_market_cap", "market_cap_to_revenue", "fdv_to_revenue"]:
        factor = next(f for f in result.factors if f.name == name)
        assert factor.insufficient_data is True


def test_revenue_factors_computed_when_revenue_snapshot_exists():
    asset, snapshot = make_asset_with_snapshot(market_cap=Decimal("100000000"), fdv=Decimal("150000000"))
    protocol = make_protocol(asset)
    RevenueSnapshot.objects.create(
        protocol=protocol, revenue_24h_usd=Decimal("50000"),  # ~18.25M/yr
        source="defillama", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    result = compute_undervaluation_score(asset, snapshot)

    revenue = next(f for f in result.factors if f.name == "revenue_to_market_cap")
    mc_to_rev = next(f for f in result.factors if f.name == "market_cap_to_revenue")
    fdv_to_rev = next(f for f in result.factors if f.name == "fdv_to_revenue")

    assert revenue.insufficient_data is False
    assert mc_to_rev.insufficient_data is False
    assert fdv_to_rev.insufficient_data is False
    # 100M / 18.25M ~= 5.48x -> just above the "cheap" cutoff of 5x, so
    # score should be high but not the full 100.
    assert Decimal("80") < mc_to_rev.normalized_value < Decimal("100")


def test_fdv_to_revenue_insufficient_without_fdv():
    asset, snapshot = make_asset_with_snapshot(market_cap=Decimal("100000000"), fdv=None)
    protocol = make_protocol(asset)
    RevenueSnapshot.objects.create(
        protocol=protocol, revenue_24h_usd=Decimal("50000"),
        source="defillama", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    result = compute_undervaluation_score(asset, snapshot)
    fdv_to_rev = next(f for f in result.factors if f.name == "fdv_to_revenue")
    assert fdv_to_rev.insufficient_data is True

    # market_cap_to_revenue should still be computed even without FDV.
    mc_to_rev = next(f for f in result.factors if f.name == "market_cap_to_revenue")
    assert mc_to_rev.insufficient_data is False


def test_full_data_undervaluation_confidence_is_high():
    asset, snapshot = make_asset_with_snapshot(market_cap=Decimal("100000000"), fdv=Decimal("150000000"))
    protocol = make_protocol(asset)
    protocol.tvl_snapshots.model.objects.create(
        protocol=protocol, tvl_usd=Decimal("200000000"), change_7d_pct=Decimal("10"),
        source="defillama", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    FeeSnapshot.objects.create(
        protocol=protocol, fees_24h_usd=Decimal("100000"), fees_30d_usd=Decimal("2500000"),
        source="defillama", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    RevenueSnapshot.objects.create(
        protocol=protocol, revenue_24h_usd=Decimal("50000"),
        source="defillama", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    result = compute_undervaluation_score(asset, snapshot)
    # Only user_activity_to_market_cap (20/100 weight) remains insufficient.
    assert result.data_confidence == Decimal("0.8000")
