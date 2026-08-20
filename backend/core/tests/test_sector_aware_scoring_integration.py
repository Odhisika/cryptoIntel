from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.models import Asset, MarketSnapshot, Protocol, TVLSnapshot
from core.scoring.potential_10x import compute_10x_potential_score
from core.scoring.undervaluation import compute_undervaluation_score

pytestmark = pytest.mark.django_db


def make_asset_with_snapshot(sector, market_cap=Decimal("100000000")):
    asset = Asset.objects.create(
        symbol="tst", name="Test Coin", external_ids={"coingecko": "test"}, sector=sector
    )
    snapshot = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("1"), market_cap_usd=market_cap,
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    return asset, snapshot


# --- Undervaluation Score ---

def test_l1_sector_zeroes_tvl_and_growth_factors():
    asset, snapshot = make_asset_with_snapshot(Asset.Sector.L1)
    result = compute_undervaluation_score(asset, snapshot)
    tvl = next(f for f in result.factors if f.name == "tvl_to_market_cap")
    growth = next(f for f in result.factors if f.name == "fundamentals_growth")
    assert tvl.weight == Decimal("0")
    assert growth.weight == Decimal("0")
    assert "Not applicable" in tvl.note
    assert "Not applicable" in growth.note


def test_defi_sector_keeps_tvl_factor_at_base_weight():
    asset, snapshot = make_asset_with_snapshot(Asset.Sector.DEFI)
    result = compute_undervaluation_score(asset, snapshot)
    tvl = next(f for f in result.factors if f.name == "tvl_to_market_cap")
    assert tvl.weight == Decimal("20")  # base weight, unchanged


def test_unclassified_asset_keeps_base_weights():
    asset, snapshot = make_asset_with_snapshot(sector=None)
    result = compute_undervaluation_score(asset, snapshot)
    tvl = next(f for f in result.factors if f.name == "tvl_to_market_cap")
    assert tvl.weight == Decimal("20")


def test_l1_sector_redistributes_weight_to_other_factors():
    asset, snapshot = make_asset_with_snapshot(Asset.Sector.L1)
    result = compute_undervaluation_score(asset, snapshot)
    revenue = next(f for f in result.factors if f.name == "revenue_to_market_cap")
    # Base weight 15 should now be higher since 40 total weight (tvl 20 +
    # growth 20) was redistributed proportionally across the other 5
    # factors (15+15+20+5+5=60 remaining base), so revenue's new weight
    # = 15 + (15/60)*40 = 15 + 10 = 25.
    assert revenue.weight == Decimal("25")


def test_total_weight_still_sums_to_100_for_non_tvl_sector():
    asset, snapshot = make_asset_with_snapshot(Asset.Sector.MEME)
    result = compute_undervaluation_score(asset, snapshot)
    assert sum(f.weight for f in result.factors) == Decimal("100")


# --- 10X Potential Score ---

def test_fundamentals_zeroed_for_non_tvl_sector():
    asset, snapshot = make_asset_with_snapshot(Asset.Sector.MEME)
    result = compute_10x_potential_score(asset, snapshot)
    fundamentals = next(f for f in result.factors if f.name == "fundamentals")
    assert fundamentals.weight == Decimal("0")
    assert fundamentals.insufficient_data is True
    assert "Not applicable" in fundamentals.note


def test_fundamentals_kept_at_base_weight_for_defi_sector():
    asset, snapshot = make_asset_with_snapshot(Asset.Sector.DEX)
    protocol = Protocol.objects.create(asset=asset, slug="tst-protocol", name="TST")
    TVLSnapshot.objects.create(
        protocol=protocol, tvl_usd=Decimal("200000000"), source="defillama",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    result = compute_10x_potential_score(asset, snapshot)
    fundamentals = next(f for f in result.factors if f.name == "fundamentals")
    assert fundamentals.weight == Decimal("15")  # base weight
    assert fundamentals.insufficient_data is False


def test_10x_potential_total_weight_still_sums_to_100():
    asset, snapshot = make_asset_with_snapshot(Asset.Sector.DEPIN)
    result = compute_10x_potential_score(asset, snapshot)
    assert sum(f.weight for f in result.factors) == Decimal("100")


def test_meme_coin_score_not_capped_by_missing_tvl():
    # Before Phase 7, a memecoin with no TVL data would have its
    # "fundamentals" factor sitting at insufficient_data with a full
    # 15-weight slot doing nothing useful. After Phase 7, that weight is
    # redistributed, so a memecoin with strong data on its OTHER factors
    # can still reach a high data_confidence despite never having TVL.
    asset, snapshot = make_asset_with_snapshot(Asset.Sector.MEME, market_cap=Decimal("20000000"))
    result = compute_10x_potential_score(asset, snapshot)
    # market_cap_opportunity's weight should be higher than its 15 base,
    # since fundamentals' freed weight was redistributed onto it (and 8
    # other factors).
    mc_opp = next(f for f in result.factors if f.name == "market_cap_opportunity")
    assert mc_opp.weight > Decimal("15")
