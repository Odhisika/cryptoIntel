from io import StringIO

import pytest
import responses
from django.core.management import call_command

from core.models import Asset, Blockchain, ContractAddress
from core.providers.coingecko import COINGECKO_BASE_URL

pytestmark = pytest.mark.django_db

COIN_DETAIL_PAYLOAD = {
    "id": "uniswap",
    "platforms": {
        "ethereum": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
        "arbitrum-one": "0xfa7f8980b0f1e64a2062791cc3b0871572f1f7f0",
        "some-native-chain": "",  # empty address should be filtered out
    },
}


@responses.activate
def test_creates_contract_addresses_and_blockchains():
    Asset.objects.create(symbol="uni", name="Uniswap", external_ids={"coingecko": "uniswap"})
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/uniswap", json=COIN_DETAIL_PAYLOAD, status=200
    )

    out = StringIO()
    call_command("populate_contract_addresses", stdout=out)

    assert ContractAddress.objects.count() == 2  # empty-address platform filtered out
    assert Blockchain.objects.filter(slug="ethereum").exists()
    assert Blockchain.objects.filter(slug="arbitrum-one").exists()
    assert "2 contract addresses created" in out.getvalue()


@responses.activate
def test_skips_assets_that_already_have_addresses():
    asset = Asset.objects.create(symbol="uni", name="Uniswap", external_ids={"coingecko": "uniswap"})
    chain = Blockchain.objects.create(slug="ethereum", name="Ethereum")
    ContractAddress.objects.create(asset=asset, blockchain=chain, address="0xexisting")

    out = StringIO()
    call_command("populate_contract_addresses", stdout=out)

    # No HTTP call should have been made at all for this asset.
    assert len(responses.calls) == 0
    assert "1 assets already had" in out.getvalue()


@responses.activate
def test_dry_run_does_not_write():
    Asset.objects.create(symbol="uni", name="Uniswap", external_ids={"coingecko": "uniswap"})
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/uniswap", json=COIN_DETAIL_PAYLOAD, status=200
    )

    call_command("populate_contract_addresses", "--dry-run")

    assert ContractAddress.objects.count() == 0


@responses.activate
def test_limit_caps_number_of_assets_processed():
    Asset.objects.create(symbol="a", name="A", external_ids={"coingecko": "coin-a"})
    Asset.objects.create(symbol="b", name="B", external_ids={"coingecko": "coin-b"})
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/coin-a", json=COIN_DETAIL_PAYLOAD, status=200
    )

    call_command("populate_contract_addresses", "--limit=1")

    assert len(responses.calls) == 1
