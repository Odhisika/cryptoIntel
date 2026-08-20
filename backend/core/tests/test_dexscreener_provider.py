from datetime import datetime, timezone
from decimal import Decimal

import pytest
import responses

from core.providers import DEXPairData, DEXScreenerProvider, ProviderError
from core.providers.dexscreener import (
    DEXSCREENER_BASE_URL,
    _normalize_chain,
    _parse_ts_ms,
    _safe_decimal,
    _safe_int,
)


@pytest.fixture
def provider():
    return DEXScreenerProvider(max_retries=2)


SAMPLE_PAIR_ROW = {
    "chainId": "ethereum",
    "dexId": "uniswap",
    "pairAddress": "0xaaaa1111bbbb2222cccc3333dddd4444eeee5555",
    "baseToken": {
        "address": "0x1111111111111111111111111111111111111111",
        "symbol": "TEST",
    },
    "quoteToken": {
        "address": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
        "symbol": "WETH",
    },
    "priceUsd": "1.2345",
    "fdv": 5000000,
    "marketCap": 3000000,
    "liquidity": {"usd": 250000},
    "volume": {"h24": 800000, "h6": 200000, "h1": 50000},
    "priceChange": {"h24": 12.5, "h6": 3.1, "h1": -0.8},
    "txns": {"h24": {"buys": 1500, "sells": 1200}},
    "pairCreatedAt": 1700000000000,
}

SAMPLE_PROFILE_ROW = {
    "chainId": "solana",
    "tokenAddress": "So11111111111111111111111111111111111111112",
    "description": "Wrapped SOL",
}


# --- Helper function tests ---


class TestNormalizeChain:
    def test_known_aliases(self):
        assert _normalize_chain("eth") == "ethereum"
        assert _normalize_chain("sol") == "solana"
        assert _normalize_chain("bsc") == "bsc"
        assert _normalize_chain("binance-smart-chain") == "bsc"
        assert _normalize_chain("matic") == "polygon"
        assert _normalize_chain("arb") == "arbitrum"
        assert _normalize_chain("avax") == "avalanche"
        assert _normalize_chain("ftm") == "fantom"
        assert _normalize_chain("op") == "optimism"
        assert _normalize_chain("xdai") == "gnosis"

    def test_canonical_names_passthrough(self):
        assert _normalize_chain("ethereum") == "ethereum"
        assert _normalize_chain("base") == "base"
        assert _normalize_chain("cronos") == "cronos"

    def test_case_insensitive(self):
        assert _normalize_chain("Ethereum") == "ethereum"
        assert _normalize_chain("SOLANA") == "solana"
        assert _normalize_chain(" Arbitrum ") == "arbitrum"

    def test_unknown_chain_stays_lower(self):
        assert _normalize_chain("my-custom-chain") == "my-custom-chain"


class TestSafeDecimal:
    def test_valid_number(self):
        assert _safe_decimal(123) == Decimal("123")
        assert _safe_decimal("45.67") == Decimal("45.67")

    def test_none_returns_none(self):
        assert _safe_decimal(None) is None

    def test_invalid_returns_none(self):
        assert _safe_decimal("not-a-number") is None
        assert _safe_decimal([]) is None


class TestSafeInt:
    def test_valid(self):
        assert _safe_int(42) == 42
        assert _safe_int("100") == 100

    def test_none_returns_none(self):
        assert _safe_int(None) is None

    def test_invalid(self):
        assert _safe_int("abc") is None


class TestParseTsMs:
    def test_valid_timestamp(self):
        result = _parse_ts_ms(1700000000000)
        assert result == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)

    def test_none_returns_none(self):
        assert _parse_ts_ms(None) is None

    def test_invalid_returns_none(self):
        assert _parse_ts_ms("not-a-ts") is None


# --- _parse_pair tests ---


class TestParsePair:
    def test_valid_row(self, provider):
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        pair = provider._parse_pair(SAMPLE_PAIR_ROW, now)

        assert pair is not None
        assert pair.chain == "ethereum"
        assert pair.dex_name == "uniswap"
        assert pair.pair_address == "0xaaaa1111bbbb2222cccc3333dddd4444eeee5555"
        assert pair.base_token_address == "0x1111111111111111111111111111111111111111"
        assert pair.base_token_symbol == "TEST"
        assert pair.quote_token_symbol == "WETH"
        assert pair.price_usd == Decimal("1.2345")
        assert pair.fdv_usd == Decimal("5000000")
        assert pair.market_cap_usd == Decimal("3000000")
        assert pair.liquidity_usd == Decimal("250000")
        assert pair.volume_24h_usd == Decimal("800000")
        assert pair.volume_6h_usd == Decimal("200000")
        assert pair.volume_1h_usd == Decimal("50000")
        assert pair.price_change_24h_pct == Decimal("12.5")
        assert pair.price_change_6h_pct == Decimal("3.1")
        assert pair.price_change_1h_pct == Decimal("-0.8")
        assert pair.txns_24h_buys == 1500
        assert pair.txns_24h_sells == 1200
        assert pair.pair_created_at == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
        assert pair.observed_at == now
        assert pair.source == "dexscreener"

    def test_missing_chain_returns_none(self, provider):
        row = dict(SAMPLE_PAIR_ROW)
        del row["chainId"]
        assert provider._parse_pair(row, datetime.now(timezone.utc)) is None

    def test_missing_pair_address_returns_none(self, provider):
        row = dict(SAMPLE_PAIR_ROW)
        row["pairAddress"] = ""
        assert provider._parse_pair(row, datetime.now(timezone.utc)) is None

    def test_missing_price_returns_none(self, provider):
        row = dict(SAMPLE_PAIR_ROW)
        row["priceUsd"] = None
        assert provider._parse_pair(row, datetime.now(timezone.utc)) is None

    def test_missing_optional_fields_use_defaults(self, provider):
        minimal_row = {
            "chainId": "base",
            "pairAddress": "0xdeadbeef",
            "priceUsd": "0.50",
            "baseToken": {"symbol": "T"},
            "quoteToken": {},
        }
        pair = provider._parse_pair(minimal_row, datetime.now(timezone.utc))
        assert pair is not None
        assert pair.fdv_usd is None
        assert pair.market_cap_usd is None
        assert pair.volume_24h_usd is None
        assert pair.txns_24h_buys is None
        assert pair.txns_24h_sells is None
        assert pair.pair_created_at is None

    def test_chain_alias_normalized(self, provider):
        row = dict(SAMPLE_PAIR_ROW)
        row["chainId"] = "arb"
        pair = provider._parse_pair(row, datetime.now(timezone.utc))
        assert pair.chain == "arbitrum"

    def test_missing_nested_volume_returns_none(self, provider):
        row = dict(SAMPLE_PAIR_ROW)
        row["volume"] = {}
        pair = provider._parse_pair(row, datetime.now(timezone.utc))
        assert pair is not None
        assert pair.volume_24h_usd is None


# --- fetch_pairs_by_tokens tests ---


class TestFetchPairsByTokens:
    @responses.activate
    def test_empty_input_returns_empty(self, provider):
        assert provider.fetch_pairs_by_tokens([]) == []

    @responses.activate
    def test_single_token(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/tokens/v1/ethereum/0x1111111111111111111111111111111111111111",
            json=[SAMPLE_PAIR_ROW],
            status=200,
        )

        tokens = [{"address": "0x1111111111111111111111111111111111111111", "chain": "ethereum"}]
        result = provider.fetch_pairs_by_tokens(tokens)

        assert len(result) == 1
        assert result[0].base_token_symbol == "TEST"

    @responses.activate
    def test_multiple_tokens_same_chain_batched(self, provider):
        addresses = [f"0x{'a' * 40}", f"0x{'b' * 40}"]
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/tokens/v1/ethereum/{addresses[0]},{addresses[1]}",
            json=[
                {**SAMPLE_PAIR_ROW, "pairAddress": addresses[0]},
                {**SAMPLE_PAIR_ROW, "pairAddress": addresses[1]},
            ],
            status=200,
        )

        tokens = [{"address": a, "chain": "ethereum"} for a in addresses]
        result = provider.fetch_pairs_by_tokens(tokens)
        assert len(result) == 2

    @responses.activate
    def test_tokens_across_multiple_chains(self, provider):
        eth_addr = "0x" + "a" * 40
        sol_addr = "So11111111111111111111111111111111111111112"

        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/tokens/v1/ethereum/{eth_addr}",
            json=[SAMPLE_PAIR_ROW],
            status=200,
        )
        sol_row = {**SAMPLE_PAIR_ROW, "chainId": "solana", "pairAddress": "pair123"}
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/tokens/v1/solana/{sol_addr.lower()}",
            json=[sol_row],
            status=200,
        )

        tokens = [
            {"address": eth_addr, "chain": "ethereum"},
            {"address": sol_addr, "chain": "solana"},
        ]
        result = provider.fetch_pairs_by_tokens(tokens)
        assert len(result) == 2

    @responses.activate
    def test_chain_error_silently_skipped(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/tokens/v1/badchain/0xabc",
            status=400,
        )

        tokens = [{"address": "0xabc", "chain": "badchain"}]
        result = provider.fetch_pairs_by_tokens(tokens)
        assert result == []

    @responses.activate
    def test_non_list_payload_skipped(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/tokens/v1/ethereum/0xabc",
            json={"error": "unexpected"},
            status=200,
        )

        tokens = [{"address": "0xabc", "chain": "ethereum"}]
        result = provider.fetch_pairs_by_tokens(tokens)
        assert result == []

    @responses.activate
    def test_unparseable_rows_filtered(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/tokens/v1/ethereum/0xabc",
            json=[{"chainId": "", "pairAddress": ""}],
            status=200,
        )

        tokens = [{"address": "0xabc", "chain": "ethereum"}]
        result = provider.fetch_pairs_by_tokens(tokens)
        assert result == []


# --- fetch_trending_pairs tests ---


class TestFetchTrendingPairs:
    @responses.activate
    def test_happy_path(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
            json=[SAMPLE_PROFILE_ROW],
            status=200,
        )

        result = provider.fetch_trending_pairs()

        assert len(result) == 1
        pair = result[0]
        assert pair.chain == "solana"
        assert pair.base_token_address == "so11111111111111111111111111111111111111112"
        assert pair.dex_name == "unknown"
        assert pair.base_token_symbol == "Wrapped SOL"[:20]
        assert pair.price_usd == Decimal("0")
        assert pair.source == "dexscreener"

    @responses.activate
    def test_empty_list(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
            json=[],
            status=200,
        )
        assert provider.fetch_trending_pairs() == []

    @responses.activate
    def test_non_list_payload(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
            json={"error": "bad"},
            status=200,
        )
        assert provider.fetch_trending_pairs() == []

    @responses.activate
    def test_rows_missing_chain_skipped(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
            json=[{"chainId": "", "tokenAddress": "0xabc"}],
            status=200,
        )
        assert provider.fetch_trending_pairs() == []

    @responses.activate
    def test_rows_missing_address_skipped(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
            json=[{"chainId": "ethereum", "tokenAddress": ""}],
            status=200,
        )
        assert provider.fetch_trending_pairs() == []


# --- Retry and error handling tests ---


class TestRetryLogic:
    @responses.activate
    def test_rate_limit_retried_then_succeeds(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
            status=429,
            headers={"Retry-After": "0"},
        )
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
            json=[SAMPLE_PROFILE_ROW],
            status=200,
        )

        result = provider.fetch_trending_pairs()
        assert len(result) == 1

    @responses.activate
    def test_500_retried_then_succeeds(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
            status=500,
        )
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
            json=[SAMPLE_PROFILE_ROW],
            status=200,
        )

        result = provider.fetch_trending_pairs()
        assert len(result) == 1

    @responses.activate
    def test_400_not_retried(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
            json={"error": "bad request"},
            status=400,
        )

        with pytest.raises(ProviderError) as exc_info:
            provider.fetch_trending_pairs()

        assert exc_info.value.retryable is False
        assert len(responses.calls) == 1

    @responses.activate
    def test_429_exhausts_retries(self, provider):
        for _ in range(2):
            responses.add(
                responses.GET,
                f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
                status=429,
                headers={"Retry-After": "0"},
            )

        with pytest.raises(ProviderError) as exc_info:
            provider.fetch_trending_pairs()

        assert exc_info.value.retryable is True
        assert len(responses.calls) == 2

    @responses.activate
    def test_500_exhausts_retries(self, provider):
        for _ in range(3):
            responses.add(
                responses.GET,
                f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
                status=500,
            )

        with pytest.raises(ProviderError) as exc_info:
            provider.fetch_trending_pairs()

        assert exc_info.value.retryable is True

    @responses.activate
    def test_500_error_message_content(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
            status=500,
        )
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
            json=[SAMPLE_PROFILE_ROW],
            status=200,
        )
        provider.fetch_trending_pairs()
        assert len(responses.calls) == 2

    @responses.activate
    def test_403_is_not_retried(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
            json={"error": "forbidden"},
            status=403,
        )

        with pytest.raises(ProviderError) as exc_info:
            provider.fetch_trending_pairs()

        assert exc_info.value.retryable is False
        assert len(responses.calls) == 1

    @responses.activate
    def test_404_is_not_retried(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1",
            json={"error": "not found"},
            status=404,
        )

        with pytest.raises(ProviderError) as exc_info:
            provider.fetch_trending_pairs()

        assert exc_info.value.retryable is False
        assert len(responses.calls) == 1


# --- Batch aggregation ---


class TestBatchAggregation:
    @responses.activate
    def test_addresses_lowercased_per_chain(self, provider):
        mixed_case = "0xAaBbCcDdEeFf0011223344556677889900112233"
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/tokens/v1/ethereum/{mixed_case.lower()}",
            json=[SAMPLE_PAIR_ROW],
            status=200,
        )

        tokens = [{"address": mixed_case, "chain": "ethereum"}]
        result = provider.fetch_pairs_by_tokens(tokens)
        assert len(responses.calls) == 1
        assert mixed_case.lower() in responses.calls[0].request.url
        assert len(result) == 1

    @responses.activate
    def test_chain_alias_used_in_request(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/tokens/v1/solana/someaddr",
            json=[SAMPLE_PAIR_ROW],
            status=200,
        )

        tokens = [{"address": "someaddr", "chain": "SOL"}]
        provider.fetch_pairs_by_tokens(tokens)
        assert "/tokens/v1/solana/" in responses.calls[0].request.url

    @responses.activate
    def test_mixed_valid_and_invalid_rows(self, provider):
        responses.add(
            responses.GET,
            f"{DEXSCREENER_BASE_URL}/tokens/v1/ethereum/0xabc",
            json=[
                SAMPLE_PAIR_ROW,
                {"chainId": ""},
            ],
            status=200,
        )

        tokens = [{"address": "0xabc", "chain": "ethereum"}]
        result = provider.fetch_pairs_by_tokens(tokens)
        assert len(result) == 1
