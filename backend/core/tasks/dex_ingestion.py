"""
DEX Screener data ingestion task.

Fetches DEX pair data (liquidity, volume, buy/sell activity, token age)
for every active Asset that has contract addresses, and writes a
DEXPairSnapshot for each.

Multiple DEX pairs per token are AGGREGATED at this layer:
  - Liquidity: summed across all pairs
  - Volume: summed across all pairs
  - Price: weighted by liquidity across pairs
  - Buy/sell counts: summed across pairs
  - Pair creation time: earliest across pairs
  - Chains: union of all chains

This aggregation means the scoring engine sees one DEX snapshot per
token, not a flood of per-pair data it would need to reconcile itself.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from celery import shared_task
from django.db import transaction

from core.models import Asset, ContractAddress, DataIngestionJob, DEXPairSnapshot
from core.providers import DEXScreenerProvider, ProviderError

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def ingest_dex_screener_data(self):
    """Fetch DEX pair data for every active Asset with contract addresses."""

    job = DataIngestionJob.objects.create(provider="dexscreener", job_type="dex_pair_snapshot")

    # Build the token-address list for DEX Screener, grouped by asset
    assets_with_contracts = []
    token_addresses = []
    for asset in Asset.objects.filter(is_active=True).prefetch_related("contract_addresses"):
        contracts = list(asset.contract_addresses.select_related("blockchain"))
        if not contracts:
            continue
        assets_with_contracts.append(asset)
        for ca in contracts:
            token_addresses.append({
                "address": ca.address,
                "chain": ca.blockchain.slug,
                "_asset_id": str(asset.id),
            })

    job.assets_attempted = len(assets_with_contracts)
    job.save(update_fields=["assets_attempted"])

    if not assets_with_contracts:
        job.status = DataIngestionJob.Status.SUCCESS
        job.finished_at = datetime.now(timezone.utc)
        job.error_summary = "No assets with contract addresses to ingest DEX data for."
        job.save()
        return {"attempted": 0, "succeeded": 0}

    provider = DEXScreenerProvider()

    try:
        pairs = provider.fetch_pairs_by_tokens(token_addresses)
    except ProviderError as exc:
        job.status = DataIngestionJob.Status.FAILED
        job.error_summary = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        job.save()
        if exc.retryable:
            raise self.retry(exc=exc)
        logger.error("Non-retryable DEX Screener error: %s", exc)
        return {"attempted": len(assets_with_contracts), "succeeded": 0, "error": str(exc)}

    # Aggregate pairs by asset (one asset can have multiple pairs across
    # DEXes/chains — we sum volume/liquidity, use highest-liquidity pair
    # for price changes, take earliest creation time).
    asset_id_to_contracts = {}
    for asset in assets_with_contracts:
        asset_id_to_contracts[str(asset.id)] = asset

    aggregated = _aggregate_pairs_by_asset(pairs, token_addresses)

    succeeded = 0
    now = datetime.now(timezone.utc)
    with transaction.atomic():
        for asset_id, agg in aggregated.items():
            asset = asset_id_to_contracts.get(asset_id)
            if asset is None:
                continue

            DEXPairSnapshot.objects.update_or_create(
                asset=asset,
                source="dexscreener",
                observed_at=now,
                defaults={
                    "liquidity_usd": agg["liquidity_usd"],
                    "volume_24h_usd": agg["volume_24h_usd"],
                    "volume_6h_usd": agg["volume_6h_usd"],
                    "volume_1h_usd": agg["volume_1h_usd"],
                    "price_change_24h_pct": agg["price_change_24h_pct"],
                    "price_change_6h_pct": agg["price_change_6h_pct"],
                    "price_change_1h_pct": agg["price_change_1h_pct"],
                    "txns_24h_buys": agg["txns_24h_buys"],
                    "txns_24h_sells": agg["txns_24h_sells"],
                    "earliest_pair_created_at": agg["earliest_pair_created_at"],
                    "pair_count": agg["pair_count"],
                    "chains": agg["chains"],
                },
            )
            succeeded += 1

    job.assets_succeeded = succeeded
    job.status = (
        DataIngestionJob.Status.SUCCESS if succeeded == len(assets_with_contracts)
        else DataIngestionJob.Status.PARTIAL
    )
    job.finished_at = datetime.now(timezone.utc)
    job.save()

    return {"attempted": len(assets_with_contracts), "succeeded": succeeded}


def _aggregate_pairs_by_asset(pairs, token_addresses):
    """Aggregate multiple DEX pairs per token into a single summary dict.

    For each asset, we:
    1. Sum liquidity and volume across all its pairs
    2. Use the highest-liquidity pair's price changes (most reliable)
    3. Sum buy/sell transaction counts
    4. Take the earliest pair creation time (token age)
    5. Collect all chains the token trades on
    6. Count total pairs (multi-DEX presence signal)
    """
    # Map asset_id -> list of pairs
    addr_to_asset = {}
    for item in token_addresses:
        addr_to_asset[item["address"].lower()] = item["_asset_id"]

    asset_pairs = defaultdict(list)
    for pair in pairs:
        asset_id = addr_to_asset.get(pair.base_token_address)
        if asset_id:
            asset_pairs[asset_id].append(pair)

    results = {}
    for asset_id, pair_list in asset_pairs.items():
        total_liquidity = Decimal("0")
        total_volume_24h = Decimal("0")
        total_volume_6h = Decimal("0")
        total_volume_1h = Decimal("0")
        total_buys = 0
        total_sells = 0
        earliest_created = None
        chains = set()
        best_liquidity = Decimal("0")
        best_pair = None

        for pair in pair_list:
            total_liquidity += pair.liquidity_usd
            if pair.volume_24h_usd:
                total_volume_24h += pair.volume_24h_usd
            if pair.volume_6h_usd:
                total_volume_6h += pair.volume_6h_usd
            if pair.volume_1h_usd:
                total_volume_1h += pair.volume_1h_usd
            if pair.txns_24h_buys:
                total_buys += pair.txns_24h_buys
            if pair.txns_24h_sells:
                total_sells += pair.txns_24h_sells
            if pair.pair_created_at:
                if earliest_created is None or pair.pair_created_at < earliest_created:
                    earliest_created = pair.pair_created_at
            chains.add(pair.chain)

            if pair.liquidity_usd > best_liquidity:
                best_liquidity = pair.liquidity_usd
                best_pair = pair

        # Use highest-liquidity pair's price changes as the "primary" signal
        results[asset_id] = {
            "liquidity_usd": total_liquidity,
            "volume_24h_usd": total_volume_24h if total_volume_24h > 0 else None,
            "volume_6h_usd": total_volume_6h if total_volume_6h > 0 else None,
            "volume_1h_usd": total_volume_1h if total_volume_1h > 0 else None,
            "price_change_24h_pct": best_pair.price_change_24h_pct if best_pair else None,
            "price_change_6h_pct": best_pair.price_change_6h_pct if best_pair else None,
            "price_change_1h_pct": best_pair.price_change_1h_pct if best_pair else None,
            "txns_24h_buys": total_buys if total_buys > 0 else None,
            "txns_24h_sells": total_sells if total_sells > 0 else None,
            "earliest_pair_created_at": earliest_created,
            "pair_count": len(pair_list),
            "chains": sorted(chains),
        }

    return results
