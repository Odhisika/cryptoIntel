from datetime import datetime, timezone
from decimal import Decimal

import pytest
import responses

from core.providers import BinanceProvider, CandleData, ProviderError, TickerData
from core.providers.binance import BINANCE_BASE_URL


@pytest.fixture
def provider():
    return BinanceProvider(max_retries=2)


SAMPLE_KLINE_ROW = [
    1700000000000,       # open_time
    "65000.00",          # open
    "66000.00",          # high
    "64500.00",          # low
    "65500.00",          # close
    "1200.5",            # volume
    1700086399999,       # close_time
    "78032500.00",       # quote_volume
    50000,               # trades
    "600.00",            # taker_buy_base
    "39000000.00",       # taker_buy_quote
    "0",                 # ignore
]

SAMPLE_TICKER_PAYLOAD = {
    "symbol": "BTCUSDT",
    "lastPrice": "65000.00",
    "volume": "25000.00",
    "priceChangePercent": "2.35",
    "quoteVolume": "1625000000.00",
    "count": "3200000",
}

SAMPLE_ALL_TICKERS = [
    {
        "symbol": "BTCUSDT",
        "lastPrice": "65000.00",
        "volume": "25000.00",
        "priceChangePercent": "2.35",
        "quoteVolume": "1625000000.00",
        "count": "3200000",
    },
    {
        "symbol": "ETHUSDT",
        "lastPrice": "3500.00",
        "volume": "150000.00",
        "priceChangePercent": "1.50",
        "quoteVolume": "525000000.00",
        "count": "5000000",
    },
    {
        "symbol": "SOLUSDT",
        "lastPrice": "120.00",
        "volume": "800000.00",
        "priceChangePercent": "-0.75",
        "quoteVolume": "96000000.00",
        "count": "2000000",
    },
    {
        "symbol": "BNBUSDT",
        "lastPrice": "600.00",
        "volume": "50000.00",
        "priceChangePercent": "0.50",
        "quoteVolume": "30000000.00",
        "count": "800000",
    },
    {
        "symbol": "BTCBUSD",
        "lastPrice": "65000.00",
        "volume": "1000.00",
        "priceChangePercent": "2.35",
        "quoteVolume": "65000000.00",
        "count": "50000",
    },
]


# --- Candle parsing ---


class TestFetchCandles:
    @responses.activate
    def test_single_candle(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/klines",
            json=[SAMPLE_KLINE_ROW],
            status=200,
        )

        candles = provider.fetch_candles("BTCUSDT", "1d", 1)
        assert len(candles) == 1

        c = candles[0]
        assert c.symbol == "BTCUSDT"
        assert c.open == Decimal("65000.00")
        assert c.high == Decimal("66000.00")
        assert c.low == Decimal("64500.00")
        assert c.close == Decimal("65500.00")
        assert c.volume == Decimal("1200.5")
        assert c.close_time == datetime(2023, 11, 15, 22, 13, 19, 999000, tzinfo=timezone.utc)

    @responses.activate
    def test_multiple_candles(self, provider):
        kline_2 = [
            1700086400000,
            "65500.00",
            "67000.00",
            "65000.00",
            "66800.00",
            "1500.0",
            1700172799999,
            "98000000.00",
            60000,
        ]
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/klines",
            json=[SAMPLE_KLINE_ROW, kline_2],
            status=200,
        )

        candles = provider.fetch_candles("BTCUSDT", "1d", 2)
        assert len(candles) == 2
        assert candles[0].close == Decimal("65500.00")
        assert candles[1].close == Decimal("66800.00")

    @responses.activate
    def test_empty_klines(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/klines",
            json=[],
            status=200,
        )

        candles = provider.fetch_candles("BTCUSDT", "1d", 1)
        assert candles == []

    @responses.activate
    def test_malformed_row_skipped(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/klines",
            json=[SAMPLE_KLINE_ROW, [1, "bad"], ["only", "four"]],
            status=200,
        )

        candles = provider.fetch_candles("BTCUSDT", "1d", 10)
        assert len(candles) == 1

    @responses.activate
    def test_non_list_payload_raises(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/klines",
            json={"error": "bad"},
            status=200,
        )

        with pytest.raises(ProviderError) as exc_info:
            provider.fetch_candles("BTCUSDT", "1d", 1)
        assert exc_info.value.retryable is False

    @responses.activate
    def test_limit_capped_at_1000(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/klines",
            json=[SAMPLE_KLINE_ROW],
            status=200,
        )

        provider.fetch_candles("BTCUSDT", "1d", 5000)
        assert "limit=1000" in responses.calls[0].request.url

    @responses.activate
    def test_symbol_uppercased(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/klines",
            json=[SAMPLE_KLINE_ROW],
            status=200,
        )

        provider.fetch_candles("btcusdt", "1d", 1)
        assert "symbol=BTCUSDT" in responses.calls[0].request.url


# --- Ticker parsing ---


class TestFetch24hTicker:
    @responses.activate
    def test_happy_path(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            json=SAMPLE_TICKER_PAYLOAD,
            status=200,
        )

        ticker = provider.fetch_24h_ticker("BTCUSDT")
        assert ticker.symbol == "BTCUSDT"
        assert ticker.price_usd == Decimal("65000.00")
        assert ticker.volume_24h_usd == Decimal("25000.00")
        assert ticker.price_change_pct == Decimal("2.35")
        assert ticker.quote_volume_24h_usd == Decimal("1625000000.00")
        assert ticker.trades_count == 3200000
        assert ticker.observed_at is not None

    @responses.activate
    def test_non_dict_payload_raises(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            json=[],
            status=200,
        )

        with pytest.raises(ProviderError) as exc_info:
            provider.fetch_24h_ticker("BTCUSDT")
        assert exc_info.value.retryable is False

    @responses.activate
    def test_missing_optional_fields(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            json={"symbol": "ETHUSDT", "lastPrice": "3500"},
            status=200,
        )

        ticker = provider.fetch_24h_ticker("ETHUSDT")
        assert ticker.symbol == "ETHUSDT"
        assert ticker.price_usd == Decimal("3500")
        assert ticker.volume_24h_usd is None
        assert ticker.price_change_pct is None
        assert ticker.trades_count is None


# --- fetch_all_usdt_tickers filtering ---


class TestFetchAllUsdtTickers:
    @responses.activate
    def test_filters_usdt_pairs_only(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            json=SAMPLE_ALL_TICKERS,
            status=200,
        )

        tickers = provider.fetch_all_usdt_tickers()
        symbols = [t.symbol for t in tickers]
        assert "BTCUSDT" in symbols
        assert "ETHUSDT" in symbols
        assert "SOLUSDT" in symbols
        assert "BNBUSDT" in symbols
        assert "BTCBUSD" not in symbols

    @responses.activate
    def test_count_matches(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            json=SAMPLE_ALL_TICKERS,
            status=200,
        )

        tickers = provider.fetch_all_usdt_tickers()
        assert len(tickers) == 4

    @responses.activate
    def test_non_list_payload_raises(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            json={"error": "bad"},
            status=200,
        )

        with pytest.raises(ProviderError) as exc_info:
            provider.fetch_all_usdt_tickers()
        assert exc_info.value.retryable is False

    @responses.activate
    def test_empty_list(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            json=[],
            status=200,
        )

        assert provider.fetch_all_usdt_tickers() == []

    @responses.activate
    def test_malformed_rows_skipped(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            json=[SAMPLE_ALL_TICKERS[0], "not-a-dict", 42],
            status=200,
        )

        tickers = provider.fetch_all_usdt_tickers()
        assert len(tickers) == 1


# --- compute_daily_moving_average ---


class TestComputeDailyMovingAverage:
    def _make_candles(self, closes: list[str]) -> list[CandleData]:
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        return [
            CandleData(
                symbol="BTCUSDT",
                open=Decimal(c),
                high=Decimal(c),
                low=Decimal(c),
                close=Decimal(c),
                volume=Decimal("100"),
                close_time=base,
            )
            for c in closes
        ]

    def test_sufficient_data(self, provider):
        candles = self._make_candles(["100", "200", "300"])
        result = provider.compute_daily_moving_average(candles, 3)
        assert result == Decimal("200.00")

    def test_insufficient_data_returns_none(self, provider):
        candles = self._make_candles(["100", "200"])
        assert provider.compute_daily_moving_average(candles, 3) is None

    def test_empty_candles_returns_none(self, provider):
        assert provider.compute_daily_moving_average([], 50) is None

    def test_uses_recent_period(self, provider):
        candles = self._make_candles(["100", "200", "300", "400", "500"])
        result = provider.compute_daily_moving_average(candles, 3)
        assert result == Decimal("400.00")

    def test_excludes_zero_closes(self, provider):
        candles = self._make_candles(["100", "0", "300"])
        result = provider.compute_daily_moving_average(candles, 3)
        assert result is None

    def test_exact_period_length(self, provider):
        candles = self._make_candles(["10", "20", "30", "40", "50"])
        result = provider.compute_daily_moving_average(candles, 5)
        assert result == Decimal("30.00")


# --- compute_btc_volume_dominance ---


class TestComputeBtcVolumeDominance:
    def test_normal_case(self, provider):
        tickers = [
            TickerData(symbol="BTCUSDT", price_usd=Decimal("65000"), volume_24h_usd=None,
                       price_change_pct=None, quote_volume_24h_usd=Decimal("1000000"),
                       trades_count=None, observed_at=datetime.now(timezone.utc)),
            TickerData(symbol="ETHUSDT", price_usd=Decimal("3500"), volume_24h_usd=None,
                       price_change_pct=None, quote_volume_24h_usd=Decimal("500000"),
                       trades_count=None, observed_at=datetime.now(timezone.utc)),
            TickerData(symbol="SOLUSDT", price_usd=Decimal("120"), volume_24h_usd=None,
                       price_change_pct=None, quote_volume_24h_usd=Decimal("500000"),
                       trades_count=None, observed_at=datetime.now(timezone.utc)),
        ]
        result = provider.compute_btc_volume_dominance(tickers)
        assert result == Decimal("50.00")

    def test_single_btc_ticker(self, provider):
        tickers = [
            TickerData(symbol="BTCUSDT", price_usd=Decimal("65000"), volume_24h_usd=None,
                       price_change_pct=None, quote_volume_24h_usd=Decimal("2000000"),
                       trades_count=None, observed_at=datetime.now(timezone.utc)),
        ]
        result = provider.compute_btc_volume_dominance(tickers)
        assert result == Decimal("100.00")

    def test_no_btc_ticker(self, provider):
        tickers = [
            TickerData(symbol="ETHUSDT", price_usd=Decimal("3500"), volume_24h_usd=None,
                       price_change_pct=None, quote_volume_24h_usd=Decimal("1000000"),
                       trades_count=None, observed_at=datetime.now(timezone.utc)),
        ]
        result = provider.compute_btc_volume_dominance(tickers)
        assert result == Decimal("0.00")

    def test_empty_tickers_returns_none(self, provider):
        assert provider.compute_btc_volume_dominance([]) is None

    def test_all_zero_volumes_returns_none(self, provider):
        tickers = [
            TickerData(symbol="BTCUSDT", price_usd=Decimal("65000"), volume_24h_usd=None,
                       price_change_pct=None, quote_volume_24h_usd=Decimal("0"),
                       trades_count=None, observed_at=datetime.now(timezone.utc)),
            TickerData(symbol="ETHUSDT", price_usd=Decimal("3500"), volume_24h_usd=None,
                       price_change_pct=None, quote_volume_24h_usd=Decimal("0"),
                       trades_count=None, observed_at=datetime.now(timezone.utc)),
        ]
        assert provider.compute_btc_volume_dominance(tickers) is None

    def test_none_quote_volume_treated_as_zero(self, provider):
        tickers = [
            TickerData(symbol="BTCUSDT", price_usd=Decimal("65000"), volume_24h_usd=None,
                       price_change_pct=None, quote_volume_24h_usd=None,
                       trades_count=None, observed_at=datetime.now(timezone.utc)),
            TickerData(symbol="ETHUSDT", price_usd=Decimal("3500"), volume_24h_usd=None,
                       price_change_pct=None, quote_volume_24h_usd=Decimal("1000000"),
                       trades_count=None, observed_at=datetime.now(timezone.utc)),
        ]
        result = provider.compute_btc_volume_dominance(tickers)
        assert result == Decimal("0.00")


# --- Retry and error handling ---


class TestRetryLogic:
    @responses.activate
    def test_rate_limit_retried_then_succeeds(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            status=429,
            headers={"Retry-After": "0"},
        )
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            json=SAMPLE_TICKER_PAYLOAD,
            status=200,
        )

        ticker = provider.fetch_24h_ticker("BTCUSDT")
        assert ticker.symbol == "BTCUSDT"

    @responses.activate
    def test_500_retried_then_succeeds(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            status=500,
        )
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            json=SAMPLE_TICKER_PAYLOAD,
            status=200,
        )

        ticker = provider.fetch_24h_ticker("BTCUSDT")
        assert ticker.symbol == "BTCUSDT"

    @responses.activate
    def test_400_not_retried(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            json={"error": "bad symbol"},
            status=400,
        )

        with pytest.raises(ProviderError) as exc_info:
            provider.fetch_24h_ticker("INVALID")

        assert exc_info.value.retryable is False
        assert len(responses.calls) == 1

    @responses.activate
    def test_429_exhausts_retries(self, provider):
        for _ in range(2):
            responses.add(
                responses.GET,
                f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
                status=429,
                headers={"Retry-After": "0"},
            )

        with pytest.raises(ProviderError) as exc_info:
            provider.fetch_24h_ticker("BTCUSDT")

        assert exc_info.value.retryable is True
        assert len(responses.calls) == 2

    @responses.activate
    def test_500_exhausts_retries(self, provider):
        for _ in range(3):
            responses.add(
                responses.GET,
                f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
                status=500,
            )

        with pytest.raises(ProviderError) as exc_info:
            provider.fetch_24h_ticker("BTCUSDT")

        assert exc_info.value.retryable is True

    @responses.activate
    def test_403_not_retried(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            json={"error": "forbidden"},
            status=403,
        )

        with pytest.raises(ProviderError) as exc_info:
            provider.fetch_24h_ticker("BTCUSDT")

        assert exc_info.value.retryable is False
        assert len(responses.calls) == 1

    @responses.activate
    def test_404_not_retried(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            json={"error": "not found"},
            status=404,
        )

        with pytest.raises(ProviderError) as exc_info:
            provider.fetch_24h_ticker("BTCUSDT")

        assert exc_info.value.retryable is False
        assert len(responses.calls) == 1

    @responses.activate
    def test_error_includes_status_code(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/ticker/24hr",
            json={"error": "bad"},
            status=400,
        )

        with pytest.raises(ProviderError) as exc_info:
            provider.fetch_24h_ticker("BTCUSDT")

        assert "400" in str(exc_info.value)

    @responses.activate
    def test_retry_succeeds_after_initial_500(self, provider):
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/klines",
            status=500,
        )
        responses.add(
            responses.GET,
            f"{BINANCE_BASE_URL}/api/v3/klines",
            json=[SAMPLE_KLINE_ROW],
            status=200,
        )

        candles = provider.fetch_candles("BTCUSDT", "1d", 1)
        assert len(candles) == 1
        assert len(responses.calls) == 2
