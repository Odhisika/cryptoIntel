from decimal import Decimal

import pytest
import responses

from core.providers.base import ProviderError
from core.providers.onchain import COINGECKO_BASE_URL, CoinGeckoOnChainProvider

ADDRESS = "0xdac17f958d2ee523a2206206994597c13d831ec"

FULL_PAYLOAD = {
    "data": {
        "attributes": {
            "holders": {
                "count": 47911,
                "distribution_percentage": {
                    "top_10": "73.7977", "11_20": "8.7309", "21_40": "5.6147", "rest": "11.8567",
                },
                "last_updated": "2026-05-27T17:41:13Z",
            }
        }
    }
}

NO_HOLDERS_PAYLOAD = {"data": {"attributes": {}}}


@pytest.fixture
def provider():
    return CoinGeckoOnChainProvider(max_retries=2)


@responses.activate
def test_fetch_holder_data_parses_count_and_concentration(provider):
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/onchain/networks/eth/tokens/{ADDRESS}/info",
        json=FULL_PAYLOAD, status=200,
    )
    data = provider.fetch_holder_data("ethereum", ADDRESS)
    assert data.holder_count == 47911
    assert data.top_10_concentration_pct == Decimal("73.7977")
    assert data.network == "eth"


@responses.activate
def test_missing_holders_data_returns_none_not_error(provider):
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/onchain/networks/eth/tokens/{ADDRESS}/info",
        json=NO_HOLDERS_PAYLOAD, status=200,
    )
    data = provider.fetch_holder_data("ethereum", ADDRESS)
    assert data.holder_count is None
    assert data.top_10_concentration_pct is None


def test_unmapped_asset_platform_raises_non_retryable(provider):
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_holder_data("some-unmapped-chain", ADDRESS)
    assert exc_info.value.retryable is False


@responses.activate
def test_fetch_holder_distribution_wraps_holder_data(provider):
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/onchain/networks/eth/tokens/{ADDRESS}/info",
        json=FULL_PAYLOAD, status=200,
    )
    result = provider.fetch_holder_distribution(ADDRESS, "ethereum")
    assert result["mocked"] is False
    assert result["holders"] == 47911
    assert result["top_10_concentration_pct"] == Decimal("73.7977")


@responses.activate
def test_client_error_not_retried(provider):
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/onchain/networks/eth/tokens/bad-address/info",
        json={}, status=404,
    )
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_holder_data("ethereum", "bad-address")
    assert exc_info.value.retryable is False
    assert len(responses.calls) == 1


@responses.activate
def test_rate_limit_retried(provider):
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/onchain/networks/eth/tokens/{ADDRESS}/info",
        status=429, headers={"Retry-After": "0"},
    )
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/onchain/networks/eth/tokens/{ADDRESS}/info",
        json=FULL_PAYLOAD, status=200,
    )
    data = provider.fetch_holder_data("ethereum", ADDRESS)
    assert data.holder_count == 47911
