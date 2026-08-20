from datetime import datetime, timezone
from decimal import Decimal

import pytest
from django.db import IntegrityError

from core.models import Asset, Blockchain, ContractAddress, MarketSnapshot

pytestmark = pytest.mark.django_db


def make_asset(**kwargs):
    defaults = dict(symbol="btc", name="Bitcoin", external_ids={"coingecko": "bitcoin"})
    defaults.update(kwargs)
    return Asset.objects.create(**defaults)


def test_asset_str():
    asset = make_asset()
    assert "Bitcoin" in str(asset)
    assert "BTC" in str(asset)


def test_market_snapshot_is_append_only_per_source_and_time():
    asset = make_asset()
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)

    MarketSnapshot.objects.create(
        asset=asset, price_usd=Decimal("100"), source="coingecko", observed_at=ts
    )

    # Same asset+source+observed_at should violate the uniqueness constraint —
    # snapshots must never be silently duplicated for the same instant.
    with pytest.raises(IntegrityError):
        MarketSnapshot.objects.create(
            asset=asset, price_usd=Decimal("999"), source="coingecko", observed_at=ts
        )


def test_market_snapshot_history_is_preserved_across_observations():
    asset = make_asset()
    MarketSnapshot.objects.create(
        asset=asset,
        price_usd=Decimal("100"),
        source="coingecko",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    MarketSnapshot.objects.create(
        asset=asset,
        price_usd=Decimal("110"),
        source="coingecko",
        observed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    history = MarketSnapshot.objects.filter(asset=asset).order_by("observed_at")
    assert list(history.values_list("price_usd", flat=True)) == [Decimal("100"), Decimal("110")]


def test_contract_address_unique_per_chain():
    asset = make_asset()
    chain = Blockchain.objects.create(slug="ethereum", name="Ethereum")
    ContractAddress.objects.create(asset=asset, blockchain=chain, address="0xabc")

    with pytest.raises(IntegrityError):
        ContractAddress.objects.create(asset=asset, blockchain=chain, address="0xabc")


def test_contract_address_same_address_allowed_on_different_chain():
    asset = make_asset()
    eth = Blockchain.objects.create(slug="ethereum", name="Ethereum")
    bsc = Blockchain.objects.create(slug="bsc", name="BNB Smart Chain")

    ContractAddress.objects.create(asset=asset, blockchain=eth, address="0xabc")
    # Should not raise — same address string is valid on a different chain.
    ContractAddress.objects.create(asset=asset, blockchain=bsc, address="0xabc")
