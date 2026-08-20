import pytest
import responses

from core.providers.coingecko import COINGECKO_BASE_URL, CoinGeckoProvider


@responses.activate
def test_fetch_platforms_filters_empty_addresses():
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/uniswap",
        json={
            "id": "uniswap",
            "platforms": {
                "ethereum": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
                "native-chain": "",
            },
        },
        status=200,
    )
    provider = CoinGeckoProvider()
    platforms = provider.fetch_platforms("uniswap")
    assert platforms == {"ethereum": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"}


@responses.activate
def test_fetch_platforms_handles_missing_platforms_key():
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/some-coin", json={"id": "some-coin"}, status=200
    )
    provider = CoinGeckoProvider()
    assert provider.fetch_platforms("some-coin") == {}
