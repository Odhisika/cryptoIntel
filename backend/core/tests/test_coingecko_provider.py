from decimal import Decimal

import pytest
import responses

from core.providers import CoinGeckoProvider, ProviderError
from core.providers.coingecko import COINGECKO_BASE_URL


@pytest.fixture
def provider():
    return CoinGeckoProvider(max_retries=2)


SAMPLE_ROW = {
    "id": "bitcoin",
    "symbol": "btc",
    "name": "Bitcoin",
    "current_price": 65000.5,
    "market_cap": 1280000000000,
    "fully_diluted_valuation": 1365000000000,
    "total_volume": 30000000000,
    "circulating_supply": 19700000,
    "total_supply": 19700000,
    "max_supply": 21000000,
}


@responses.activate
def test_fetch_market_snapshot_happy_path(provider):
    responses.add(
        responses.GET,
        f"{COINGECKO_BASE_URL}/coins/markets",
        json=[SAMPLE_ROW],
        status=200,
    )

    result = provider.fetch_market_snapshot(["bitcoin"])

    assert len(result) == 1
    snap = result[0]
    assert snap.external_id == "bitcoin"
    assert snap.symbol == "btc"
    assert snap.price_usd == Decimal("65000.5")
    assert snap.market_cap_usd == Decimal("1280000000000")
    assert snap.source == "coingecko"


@responses.activate
def test_fetch_market_snapshot_empty_ids_short_circuits(provider):
    # No HTTP call should be registered/made for an empty id list.
    result = provider.fetch_market_snapshot([])
    assert result == []


@responses.activate
def test_rate_limit_is_retried_then_succeeds(provider):
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/markets", status=429, headers={"Retry-After": "0"}
    )
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/markets", json=[SAMPLE_ROW], status=200
    )

    result = provider.fetch_market_snapshot(["bitcoin"])
    assert len(result) == 1


@responses.activate
def test_client_error_is_not_retried(provider):
    responses.add(
        responses.GET,
        f"{COINGECKO_BASE_URL}/coins/markets",
        json={"error": "bad request"},
        status=400,
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_market_snapshot(["not-a-real-id"])

    assert exc_info.value.retryable is False
    assert len(responses.calls) == 1  # confirms no retry happened on a 4xx


@responses.activate
def test_missing_required_field_raises_non_retryable_error(provider):
    broken_row = dict(SAMPLE_ROW)
    del broken_row["current_price"]
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/markets", json=[broken_row], status=200
    )

    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_market_snapshot(["bitcoin"])

    assert exc_info.value.retryable is False


@responses.activate
def test_list_universe_filters_by_market_cap_band(provider):
    page_1 = [
        {"id": "big-coin", "market_cap": 50_000_000_000},
        {"id": "mid-coin", "market_cap": 500_000_000},
        {"id": "small-coin", "market_cap": 10_000_000},
    ]
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/markets", json=page_1, status=200
    )

    ids = provider.list_universe(min_market_cap=Decimal("50000000"), max_market_cap=Decimal("2000000000"))

    assert ids == ["mid-coin"]
