from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.models import Asset, Blockchain, ContractAddress, HolderSnapshot, MarketSnapshot
from core.scoring.potential_10x import compute_10x_potential_score
from core.scoring.risk import compute_risk_score

pytestmark = pytest.mark.django_db


def make_asset_with_snapshot_and_contract():
    asset = Asset.objects.create(symbol="uni", name="Uniswap", external_ids={"coingecko": "uniswap"})
    snapshot = MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("5"), market_cap_usd=Decimal("100000000"),
        source="coingecko", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    chain = Blockchain.objects.create(slug="ethereum", name="Ethereum")
    contract = ContractAddress.objects.create(asset=asset, blockchain=chain, address="0xabc")
    return asset, snapshot, contract


def make_holder_snapshot(contract, holder_count=None, top_10_pct=None):
    return HolderSnapshot.objects.create(
        contract_address=contract, holder_count=holder_count, top_10_concentration_pct=top_10_pct,
        source="coingecko_onchain", observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_onchain_adoption_insufficient_without_holder_data():
    asset, snapshot, contract = make_asset_with_snapshot_and_contract()
    result = compute_10x_potential_score(asset, snapshot)
    adoption = next(f for f in result.factors if f.name == "onchain_adoption")
    assert adoption.insufficient_data is True


def test_onchain_adoption_computed_from_holder_count():
    asset, snapshot, contract = make_asset_with_snapshot_and_contract()
    make_holder_snapshot(contract, holder_count=50000)
    result = compute_10x_potential_score(asset, snapshot)
    adoption = next(f for f in result.factors if f.name == "onchain_adoption")
    assert adoption.insufficient_data is False
    assert Decimal("50") < adoption.normalized_value < Decimal("100")


def test_onchain_adoption_floor_and_ceiling():
    asset_low, snap_low, contract_low = make_asset_with_snapshot_and_contract()
    make_holder_snapshot(contract_low, holder_count=50)  # below floor
    result_low = compute_10x_potential_score(asset_low, snap_low)
    adoption_low = next(f for f in result_low.factors if f.name == "onchain_adoption")
    assert adoption_low.normalized_value == Decimal("0")


def test_whale_concentration_insufficient_without_data():
    asset, snapshot, contract = make_asset_with_snapshot_and_contract()
    result = compute_risk_score(asset, snapshot)
    whale = next(f for f in result.factors if f.name == "whale_concentration_risk")
    assert whale.insufficient_data is True


def test_whale_concentration_uses_top_10_pct_directly():
    asset, snapshot, contract = make_asset_with_snapshot_and_contract()
    make_holder_snapshot(contract, holder_count=1000, top_10_pct=Decimal("85.5"))
    result = compute_risk_score(asset, snapshot)
    whale = next(f for f in result.factors if f.name == "whale_concentration_risk")
    assert whale.insufficient_data is False
    assert whale.normalized_value == Decimal("85.5")


def test_low_concentration_means_low_whale_risk():
    asset, snapshot, contract = make_asset_with_snapshot_and_contract()
    make_holder_snapshot(contract, holder_count=100000, top_10_pct=Decimal("12.0"))
    result = compute_risk_score(asset, snapshot)
    whale = next(f for f in result.factors if f.name == "whale_concentration_risk")
    assert whale.normalized_value == Decimal("12.0")
