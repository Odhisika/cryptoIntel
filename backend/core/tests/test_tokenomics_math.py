from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.models import Asset, MarketSnapshot
from core.scoring.tokenomics_math import compute_supply_inflation_pct, compute_supply_ratios

pytestmark = pytest.mark.django_db


def make_asset():
    return Asset.objects.create(symbol="tst", name="Test Coin", external_ids={"coingecko": "test"})


def snap(asset, days_ago=0, circ=None, total=None, max_supply=None):
    return MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=Decimal("100000000"),
        circulating_supply=circ, total_supply=total, max_supply=max_supply,
        source="coingecko",
        observed_at=datetime(2027, 1, 1, tzinfo=timezone.utc) - timedelta(days=days_ago),
    )


def test_supply_ratios_computed_when_all_present():
    asset = make_asset()
    s = snap(asset, circ=Decimal("500000"), total=Decimal("800000"), max_supply=Decimal("1000000"))
    ratios = compute_supply_ratios(s)
    assert ratios.circulating_to_total_pct == Decimal("62.5")
    assert ratios.circulating_to_max_pct == Decimal("50")


def test_supply_ratios_none_without_max_supply():
    asset = make_asset()
    s = snap(asset, circ=Decimal("500000"), total=Decimal("800000"), max_supply=None)
    ratios = compute_supply_ratios(s)
    assert ratios.circulating_to_max_pct is None
    assert ratios.circulating_to_total_pct == Decimal("62.5")


def test_supply_ratios_capped_at_100():
    # Data artifact: circulating > max reported (shouldn't happen, but
    # shouldn't produce a >100% ratio either).
    asset = make_asset()
    s = snap(asset, circ=Decimal("1100000"), total=Decimal("1100000"), max_supply=Decimal("1000000"))
    ratios = compute_supply_ratios(s)
    assert ratios.circulating_to_max_pct == Decimal("100")


def test_inflation_none_without_baseline_history():
    asset = make_asset()
    current = snap(asset, days_ago=0, total=Decimal("1000000"))
    assert compute_supply_inflation_pct(asset, current, months_back=12) is None


def test_inflation_computed_with_baseline_present():
    asset = make_asset()
    snap(asset, days_ago=360, total=Decimal("800000"))
    current = snap(asset, days_ago=0, total=Decimal("1000000"))
    inflation = compute_supply_inflation_pct(asset, current, months_back=12)
    assert inflation == Decimal("25")  # (1M - 800k) / 800k * 100


def test_inflation_none_when_current_total_supply_missing():
    asset = make_asset()
    snap(asset, days_ago=360, total=Decimal("800000"))
    current = snap(asset, days_ago=0, total=None)
    assert compute_supply_inflation_pct(asset, current, months_back=12) is None


def test_inflation_baseline_outside_tolerance_returns_none():
    asset = make_asset()
    snap(asset, days_ago=395, total=Decimal("800000"))  # ~30 days beyond 12M tolerance
    current = snap(asset, days_ago=0, total=Decimal("1000000"))
    assert compute_supply_inflation_pct(asset, current, months_back=12) is None
