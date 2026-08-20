from datetime import datetime, timezone
from decimal import Decimal

import pytest
import responses

from core.providers.base import ProviderError
from core.providers.defillama import DEFILLAMA_BASE_URL, DefiLlamaProvider


@pytest.fixture
def provider():
    return DefiLlamaProvider(max_retries=2)


PROTOCOLS_PAYLOAD = [
    {"slug": "uniswap", "name": "Uniswap", "gecko_id": "uniswap", "category": "Dexes",
     "tvl": 5000000000, "chains": ["Ethereum", "Arbitrum"]},
    {"slug": "no-gecko-id", "name": "No Gecko", "gecko_id": None, "category": "Lending",
     "tvl": 100000000, "chains": ["Ethereum"]},
]

PROTOCOL_DETAIL_PAYLOAD = {
    "name": "Uniswap",
    "tvl": [
        {"date": 1704067200, "totalLiquidityUSD": 4000000000},  # 8D back (relative to latest below)
        {"date": 1704672000, "totalLiquidityUSD": 4500000000},  # 1D back
        {"date": 1704758400, "totalLiquidityUSD": 5000000000},  # latest
    ],
}


@responses.activate
def test_list_protocols_parses_rows(provider):
    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/protocols", json=PROTOCOLS_PAYLOAD, status=200)

    listings = provider.list_protocols()

    assert len(listings) == 2
    uni = next(l for l in listings if l.slug == "uniswap")
    assert uni.gecko_id == "uniswap"
    assert uni.tvl_usd == Decimal("5000000000")
    assert uni.chains == ["Ethereum", "Arbitrum"]


@responses.activate
def test_list_protocols_skips_rows_without_slug(provider):
    responses.add(
        responses.GET, f"{DEFILLAMA_BASE_URL}/protocols",
        json=[{"name": "No Slug"}, PROTOCOLS_PAYLOAD[0]], status=200,
    )
    listings = provider.list_protocols()
    assert len(listings) == 1


@responses.activate
def test_fetch_protocol_tvl_computes_current_and_changes(provider):
    responses.add(
        responses.GET, f"{DEFILLAMA_BASE_URL}/protocol/uniswap", json=PROTOCOL_DETAIL_PAYLOAD, status=200
    )

    data = provider.fetch_protocol_tvl("uniswap")

    assert data.tvl_usd == Decimal("5000000000")
    assert data.change_1d_pct is not None
    assert data.change_7d_pct is None or isinstance(data.change_7d_pct, Decimal)


@responses.activate
def test_fetch_protocol_tvl_raises_on_missing_history(provider):
    responses.add(
        responses.GET, f"{DEFILLAMA_BASE_URL}/protocol/empty", json={"name": "Empty", "tvl": []}, status=200
    )
    with pytest.raises(ProviderError):
        provider.fetch_protocol_tvl("empty")


@responses.activate
def test_fetch_protocol_metrics_wraps_tvl_data(provider):
    responses.add(
        responses.GET, f"{DEFILLAMA_BASE_URL}/protocol/uniswap", json=PROTOCOL_DETAIL_PAYLOAD, status=200
    )
    result = provider.fetch_protocol_metrics("uniswap")
    assert result["mocked"] is False
    assert result["tvl_usd"] == Decimal("5000000000")


@responses.activate
def test_rate_limit_retried(provider):
    responses.add(
        responses.GET, f"{DEFILLAMA_BASE_URL}/protocols", status=429, headers={"Retry-After": "0"}
    )
    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/protocols", json=PROTOCOLS_PAYLOAD, status=200)

    listings = provider.list_protocols()
    assert len(listings) == 2


@responses.activate
def test_client_error_not_retried(provider):
    responses.add(responses.GET, f"{DEFILLAMA_BASE_URL}/protocol/bad-slug", json={}, status=404)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_protocol_tvl("bad-slug")
    assert exc_info.value.retryable is False
    assert len(responses.calls) == 1


FEES_PAYLOAD_WITH_REVENUE = {
    "name": "Uniswap", "category": "Dexes", "chains": ["Ethereum"],
    "total24h": 2000000, "total7d": 14000000, "total30d": 60000000,
    "dailyRevenue": 500000,
}

FEES_PAYLOAD_NO_REVENUE = {
    "name": "SomeDex", "category": "Dexes", "chains": ["Ethereum"],
    "total24h": 100000, "total7d": 700000, "total30d": 3000000,
    "dailyRevenue": None,
}


@responses.activate
def test_fetch_protocol_fees_with_revenue(provider):
    responses.add(
        responses.GET, f"{DEFILLAMA_BASE_URL}/summary/fees/uniswap", json=FEES_PAYLOAD_WITH_REVENUE, status=200
    )
    data = provider.fetch_protocol_fees("uniswap")
    assert data.fees_24h_usd == Decimal("2000000")
    assert data.fees_30d_usd == Decimal("60000000")
    assert data.revenue_24h_usd == Decimal("500000")


@responses.activate
def test_fetch_protocol_fees_without_revenue_is_none_not_zero(provider):
    responses.add(
        responses.GET, f"{DEFILLAMA_BASE_URL}/summary/fees/some-dex", json=FEES_PAYLOAD_NO_REVENUE, status=200
    )
    data = provider.fetch_protocol_fees("some-dex")
    assert data.fees_24h_usd == Decimal("100000")
    assert data.revenue_24h_usd is None
