from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
import responses

from core.models import Asset, Blockchain, ContractAddress, DataIngestionJob, DEXPairSnapshot
from core.providers.base import DEXPairData, ProviderError
from core.tasks.dex_ingestion import _aggregate_pairs_by_asset, ingest_dex_screener_data

pytestmark = pytest.mark.django_db


NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def make_asset(symbol="TST", name="Test Coin"):
    return Asset.objects.create(symbol=symbol, name=name, external_ids={"coingecko": "test-coin"})


def make_blockchain(slug="ethereum"):
    return Blockchain.objects.create(slug=slug, name=slug.title())


def make_contract(asset, blockchain, address="0xabc123"):
    return ContractAddress.objects.create(asset=asset, blockchain=blockchain, address=address)


def make_pair(
    base_token_address="0xabc123",
    chain="ethereum",
    dex_name="uniswap",
    liquidity_usd=Decimal("50000"),
    volume_24h_usd=Decimal("10000"),
    volume_6h_usd=Decimal("3000"),
    volume_1h_usd=Decimal("500"),
    price_change_24h_pct=Decimal("5.0"),
    price_change_6h_pct=Decimal("2.0"),
    price_change_1h_pct=Decimal("0.5"),
    txns_24h_buys=100,
    txns_24h_sells=80,
    pair_created_at=None,
    pair_address="0xpair1",
):
    return DEXPairData(
        chain=chain,
        dex_name=dex_name,
        pair_address=pair_address,
        base_token_address=base_token_address.lower(),
        base_token_symbol="TST",
        quote_token_symbol="WETH",
        price_usd=Decimal("1.50"),
        fdv_usd=Decimal("15000000"),
        market_cap_usd=Decimal("10000000"),
        liquidity_usd=liquidity_usd,
        volume_24h_usd=volume_24h_usd,
        volume_6h_usd=volume_6h_usd,
        volume_1h_usd=volume_1h_usd,
        price_change_24h_pct=price_change_24h_pct,
        price_change_6h_pct=price_change_6h_pct,
        price_change_1h_pct=price_change_1h_pct,
        txns_24h_buys=txns_24h_buys,
        txns_24h_sells=txns_24h_sells,
        pair_created_at=pair_created_at,
        observed_at=NOW,
        source="dexscreener",
    )


def token_addresses(addresses):
    return [{"address": a, "chain": "ethereum", "_asset_id": "ignored"} for a in addresses]


# ---------------------------------------------------------------------------
# _aggregate_pairs_by_asset — pure logic tests
# ---------------------------------------------------------------------------


class TestAggregatePairsByAsset:
    def test_single_pair_passthrough(self):
        pair = make_pair()
        result = _aggregate_pairs_by_asset(
            [pair],
            [{"address": "0xabc123", "chain": "ethereum", "_asset_id": "a1"}],
        )
        assert "a1" in result
        agg = result["a1"]
        assert agg["liquidity_usd"] == Decimal("50000")
        assert agg["volume_24h_usd"] == Decimal("10000")
        assert agg["pair_count"] == 1
        assert agg["chains"] == ["ethereum"]

    def test_multiple_pairs_sum_liquidity_and_volume(self):
        pair1 = make_pair(
            liquidity_usd=Decimal("30000"),
            volume_24h_usd=Decimal("8000"),
            txns_24h_buys=60,
            txns_24h_sells=40,
            pair_address="0xpair1",
            pair_created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        pair2 = make_pair(
            liquidity_usd=Decimal("20000"),
            volume_24h_usd=Decimal("5000"),
            txns_24h_buys=40,
            txns_24h_sells=30,
            pair_address="0xpair2",
            pair_created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )
        result = _aggregate_pairs_by_asset(
            [pair1, pair2],
            [{"address": "0xabc123", "chain": "ethereum", "_asset_id": "a1"}],
        )
        agg = result["a1"]
        assert agg["liquidity_usd"] == Decimal("50000")
        assert agg["volume_24h_usd"] == Decimal("13000")
        assert agg["txns_24h_buys"] == 100
        assert agg["txns_24h_sells"] == 70
        assert agg["pair_count"] == 2

    def test_earliest_pair_creation_time_wins(self):
        early = datetime(2025, 6, 1, tzinfo=timezone.utc)
        late = datetime(2026, 1, 1, tzinfo=timezone.utc)
        pair1 = make_pair(pair_created_at=late, pair_address="0xpair1")
        pair2 = make_pair(pair_created_at=early, pair_address="0xpair2")
        result = _aggregate_pairs_by_asset(
            [pair1, pair2],
            [{"address": "0xabc123", "chain": "ethereum", "_asset_id": "a1"}],
        )
        assert result["a1"]["earliest_pair_created_at"] == early

    def test_highest_liquidity_pair_determines_price_changes(self):
        low_liq_pair = make_pair(
            liquidity_usd=Decimal("10000"),
            price_change_24h_pct=Decimal("99.0"),
            price_change_6h_pct=Decimal("50.0"),
            price_change_1h_pct=Decimal("25.0"),
            pair_address="0xpair1",
        )
        high_liq_pair = make_pair(
            liquidity_usd=Decimal("100000"),
            price_change_24h_pct=Decimal("3.0"),
            price_change_6h_pct=Decimal("1.0"),
            price_change_1h_pct=Decimal("0.2"),
            pair_address="0xpair2",
        )
        result = _aggregate_pairs_by_asset(
            [low_liq_pair, high_liq_pair],
            [{"address": "0xabc123", "chain": "ethereum", "_asset_id": "a1"}],
        )
        agg = result["a1"]
        assert agg["price_change_24h_pct"] == Decimal("3.0")
        assert agg["price_change_6h_pct"] == Decimal("1.0")
        assert agg["price_change_1h_pct"] == Decimal("0.2")

    def test_chains_are_union_of_all_pairs(self):
        pair1 = make_pair(chain="ethereum", pair_address="0xpair1")
        pair2 = make_pair(chain="base", pair_address="0xpair2")
        pair3 = make_pair(chain="arbitrum", pair_address="0xpair3")
        result = _aggregate_pairs_by_asset(
            [pair1, pair2, pair3],
            [{"address": "0xabc123", "chain": "ethereum", "_asset_id": "a1"}],
        )
        assert set(result["a1"]["chains"]) == {"arbitrum", "base", "ethereum"}

    def test_none_volume_fields_are_skipped_in_sum(self):
        pair = make_pair(volume_6h_usd=None, volume_1h_usd=None)
        result = _aggregate_pairs_by_asset(
            [pair],
            [{"address": "0xabc123", "chain": "ethereum", "_asset_id": "a1"}],
        )
        agg = result["a1"]
        assert agg["volume_6h_usd"] is None
        assert agg["volume_1h_usd"] is None

    def test_all_none_volumes_give_none_not_zero(self):
        pair = make_pair(volume_24h_usd=None, volume_6h_usd=None, volume_1h_usd=None)
        result = _aggregate_pairs_by_asset(
            [pair],
            [{"address": "0xabc123", "chain": "ethereum", "_asset_id": "a1"}],
        )
        agg = result["a1"]
        assert agg["volume_24h_usd"] is None
        assert agg["volume_6h_usd"] is None
        assert agg["volume_1h_usd"] is None

    def test_none_buys_sells_give_none(self):
        pair = make_pair(txns_24h_buys=None, txns_24h_sells=None)
        result = _aggregate_pairs_by_asset(
            [pair],
            [{"address": "0xabc123", "chain": "ethereum", "_asset_id": "a1"}],
        )
        agg = result["a1"]
        assert agg["txns_24h_buys"] is None
        assert agg["txns_24h_sells"] is None

    def test_pair_with_no_matching_address_is_ignored(self):
        pair = make_pair(base_token_address="0xdeadbeef")
        result = _aggregate_pairs_by_asset(
            [pair],
            [{"address": "0xabc123", "chain": "ethereum", "_asset_id": "a1"}],
        )
        assert result == {}

    def test_unmatched_address_yields_empty_result(self):
        result = _aggregate_pairs_by_asset(
            [],
            [{"address": "0xabc123", "chain": "ethereum", "_asset_id": "a1"}],
        )
        assert result == {}

    def test_no_pair_created_at_yields_none(self):
        pair = make_pair(pair_created_at=None)
        result = _aggregate_pairs_by_asset(
            [pair],
            [{"address": "0xabc123", "chain": "ethereum", "_asset_id": "a1"}],
        )
        assert result["a1"]["earliest_pair_created_at"] is None

    def test_address_matching_is_case_insensitive(self):
        pair = make_pair(base_token_address="0xABC123")
        result = _aggregate_pairs_by_asset(
            [pair],
            [{"address": "0xabc123", "chain": "ethereum", "_asset_id": "a1"}],
        )
        assert "a1" in result

    def test_two_assets_independent_aggregation(self):
        pair_a = make_pair(base_token_address="0xaaa", liquidity_usd=Decimal("1000"), pair_address="0xp1")
        pair_b = make_pair(base_token_address="0xbbb", liquidity_usd=Decimal("2000"), pair_address="0xp2")
        token_addrs = [
            {"address": "0xaaa", "chain": "ethereum", "_asset_id": "asset_a"},
            {"address": "0xbbb", "chain": "ethereum", "_asset_id": "asset_b"},
        ]
        result = _aggregate_pairs_by_asset([pair_a, pair_b], token_addrs)
        assert result["asset_a"]["liquidity_usd"] == Decimal("1000")
        assert result["asset_b"]["liquidity_usd"] == Decimal("2000")


# ---------------------------------------------------------------------------
# ingest_dex_screener_data — full task tests with mocked provider
# ---------------------------------------------------------------------------


def _make_dex_provider(pairs):
    """Return a mock DEXScreenerProvider that returns the given pairs."""
    mock = type("MockDEXProvider", (), {"fetch_pairs_by_tokens": lambda self, addrs: pairs})()
    return mock


class TestIngestDexScreenerData:
    def test_no_assets_with_contracts_succeeds_with_zero(self):
        result = ingest_dex_screener_data()
        assert result == {"attempted": 0, "succeeded": 0}
        job = DataIngestionJob.objects.get()
        assert job.status == DataIngestionJob.Status.SUCCESS

    def test_inactive_asset_is_skipped(self):
        asset = make_asset()
        bc = make_blockchain()
        make_contract(asset, bc)
        asset.is_active = False
        asset.save()

        result = ingest_dex_screener_data()
        assert result["attempted"] == 0
        assert DEXPairSnapshot.objects.count() == 0

    @patch("core.tasks.dex_ingestion.DEXScreenerProvider")
    def test_single_asset_creates_snapshot(self, MockProvider):
        asset = make_asset()
        bc = make_blockchain()
        make_contract(asset, bc)

        pair = make_pair()
        MockProvider.return_value.fetch_pairs_by_tokens.return_value = [pair]

        result = ingest_dex_screener_data()

        assert result["succeeded"] == 1
        assert DEXPairSnapshot.objects.count() == 1
        snap = DEXPairSnapshot.objects.get()
        assert snap.liquidity_usd == Decimal("50000")
        assert snap.pair_count == 1
        assert snap.chains == ["ethereum"]

    @patch("core.tasks.dex_ingestion.DEXScreenerProvider")
    def test_provider_failure_marks_job_failed(self, MockProvider):
        asset = make_asset()
        bc = make_blockchain()
        make_contract(asset, bc)

        MockProvider.return_value.fetch_pairs_by_tokens.side_effect = ProviderError(
            "dexscreener", "timeout", retryable=False
        )

        result = ingest_dex_screener_data()
        assert result["succeeded"] == 0
        assert "error" in result
        job = DataIngestionJob.objects.get()
        assert job.status == DataIngestionJob.Status.FAILED

    @patch("core.tasks.dex_ingestion.DEXScreenerProvider")
    def test_multiple_pairs_for_same_asset_are_aggregated(self, MockProvider):
        asset = make_asset()
        bc = make_blockchain()
        make_contract(asset, bc)

        pair1 = make_pair(
            liquidity_usd=Decimal("30000"),
            volume_24h_usd=Decimal("5000"),
            pair_address="0xpair1",
            pair_created_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
        )
        pair2 = make_pair(
            liquidity_usd=Decimal("20000"),
            volume_24h_usd=Decimal("3000"),
            pair_address="0xpair2",
            pair_created_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
        )
        MockProvider.return_value.fetch_pairs_by_tokens.return_value = [pair1, pair2]

        result = ingest_dex_screener_data()
        assert result["succeeded"] == 1

        snap = DEXPairSnapshot.objects.get()
        assert snap.liquidity_usd == Decimal("50000")
        assert snap.volume_24h_usd == Decimal("8000")
        assert snap.pair_count == 2

    @patch("core.tasks.dex_ingestion.datetime")
    @patch("core.tasks.dex_ingestion.DEXScreenerProvider")
    def test_rerun_does_not_duplicate_snapshot(self, MockProvider, mock_dt):
        asset = make_asset()
        bc = make_blockchain()
        make_contract(asset, bc)

        pair = make_pair()
        MockProvider.return_value.fetch_pairs_by_tokens.return_value = [pair]

        frozen_time = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = frozen_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        ingest_dex_screener_data()
        ingest_dex_screener_data()

        # Same source + same observed_at = update_or_create deduplicates
        assert DEXPairSnapshot.objects.count() == 1

    @patch("core.tasks.dex_ingestion.DEXScreenerProvider")
    def test_one_failure_does_not_block_other_assets(self, MockProvider):
        asset_ok = make_asset("OK", "Ok Coin")
        asset_bad = make_asset("BAD", "Bad Coin")
        bc = make_blockchain()
        make_contract(asset_ok, bc, address="0xokaddr")
        make_contract(asset_bad, bc, address="0xbadaddr")

        pair_ok = make_pair(base_token_address="0xokaddr")
        MockProvider.return_value.fetch_pairs_by_tokens.return_value = [pair_ok]

        result = ingest_dex_screener_data()
        assert result["succeeded"] == 1
        job = DataIngestionJob.objects.get()
        assert job.status == DataIngestionJob.Status.PARTIAL

    @patch("core.tasks.dex_ingestion.DEXScreenerProvider")
    def test_empty_pairs_returns_zero_succeeded(self, MockProvider):
        asset = make_asset()
        bc = make_blockchain()
        make_contract(asset, bc)

        MockProvider.return_value.fetch_pairs_by_tokens.return_value = []

        result = ingest_dex_screener_data()
        assert result["succeeded"] == 0
        assert DEXPairSnapshot.objects.count() == 0

    @patch("core.tasks.dex_ingestion.DEXScreenerProvider")
    def test_asset_without_contract_addresses_is_skipped(self, MockProvider):
        make_asset()
        result = ingest_dex_screener_data()
        assert result["attempted"] == 0
        MockProvider.return_value.fetch_pairs_by_tokens.assert_not_called()
