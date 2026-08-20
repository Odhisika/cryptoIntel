from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.models import Asset, MarketSnapshot
from core.scoring.risk import compute_risk_score, risk_category

pytestmark = pytest.mark.django_db


def make_asset():
    return Asset.objects.create(symbol="tst", name="Test Coin", external_ids={"coingecko": "test-coin"})


def test_low_liquidity_scores_higher_liquidity_risk():
    asset = make_asset()
    illiquid = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        volume_24h_usd=Decimal("10000"), source="coingecko",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    result = compute_risk_score(asset, illiquid)
    liq = next(f for f in result.factors if f.name == "liquidity_risk")
    assert liq.normalized_value > Decimal("90")


def test_healthy_liquidity_scores_low_liquidity_risk():
    asset = make_asset()
    liquid = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        volume_24h_usd=Decimal("10000000"), source="coingecko",  # 10% of MC
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    result = compute_risk_score(asset, liquid)
    liq = next(f for f in result.factors if f.name == "liquidity_risk")
    assert liq.normalized_value == Decimal("0")


def test_heavy_dilution_scores_high_dilution_risk():
    asset = make_asset()
    snap = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        fully_diluted_valuation_usd=Decimal("1000000000"), source="coingecko",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    result = compute_risk_score(asset, snap)
    dilution = next(f for f in result.factors if f.name == "dilution_risk")
    assert dilution.normalized_value == Decimal("90.00")  # (1000M-100M)/1000M * 100


def test_no_fdv_marks_dilution_risk_insufficient():
    asset = make_asset()
    snap = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    result = compute_risk_score(asset, snap)
    dilution = next(f for f in result.factors if f.name == "dilution_risk")
    assert dilution.insufficient_data is True


def test_insufficient_snapshots_marks_volatility_insufficient():
    asset = make_asset()
    snap = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    result = compute_risk_score(asset, snap)
    vol = next(f for f in result.factors if f.name == "volatility_30d")
    assert vol.insufficient_data is True


def test_volatility_computed_with_enough_snapshots():
    asset = make_asset()
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prices = [Decimal("1"), Decimal("1.1"), Decimal("0.9"), Decimal("1.2"), Decimal("1.0"), Decimal("1.3")]
    snap = None
    for i, price in enumerate(prices):
        snap = MarketSnapshot.objects.create(
            asset=asset, price_usd=price, market_cap_usd=Decimal("100000000"),
            source="coingecko", observed_at=base_time + timedelta(hours=i),
        )
    result = compute_risk_score(asset, snap)
    vol = next(f for f in result.factors if f.name == "volatility_30d")
    assert vol.insufficient_data is False
    assert vol.normalized_value > Decimal("0")


def test_unimplemented_risk_factors_are_marked_insufficient():
    asset = make_asset()
    snap = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    result = compute_risk_score(asset, snap)
    missing = {f.name for f in result.missing_factors()}
    expected = {
        "liquidity_risk", "dilution_risk", "volatility_30d", "token_unlock_risk",
        "whale_concentration_risk", "dex_risk", "smart_contract_risk", "centralization_risk",
        "governance_risk", "protocol_dependency_risk", "exchange_concentration_risk",
    }
    assert missing == expected  # everything is missing for a brand new asset w/ 1 snapshot


@pytest.mark.parametrize(
    "score,expected",
    [(Decimal("10"), "LOW"), (Decimal("50"), "MEDIUM"), (Decimal("90"), "HIGH")],
)
def test_risk_category_buckets(score, expected):
    assert risk_category(score) == expected
