"""
Backfill DEX Screener data for existing assets with contract addresses.

Usage:
    python manage.py populate_dex_data
    python manage.py populate_dex_data --dry-run
"""

from datetime import datetime, timezone

from django.core.management.base import CommandError
from django.db import transaction

from core.models import Asset, ContractAddress, DEXPairSnapshot
from core.providers import DEXScreenerProvider, ProviderError
from core.tasks.dex_ingestion import _aggregate_pairs_by_asset


class Command(BaseCommand):
    help = "Backfill DEX Screener data for assets that have contract addresses."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would change without writing."
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

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

        if not assets_with_contracts:
            self.stdout.write(self.style.WARNING("No assets with contract addresses found."))
            return

        self.stdout.write(f"Fetching DEX data for {len(assets_with_contracts)} assets...")

        provider = DEXScreenerProvider()
        try:
            pairs = provider.fetch_pairs_by_tokens(token_addresses)
        except ProviderError as exc:
            raise CommandError(f"Failed to fetch DEX data: {exc}")

        asset_id_to_asset = {str(a.id): a for a in assets_with_contracts}
        aggregated = _aggregate_pairs_by_asset(pairs, token_addresses)

        now = datetime.now(timezone.utc)
        created, skipped = 0, 0

        with transaction.atomic():
            for asset_id, agg in aggregated.items():
                asset = asset_id_to_asset.get(asset_id)
                if asset is None:
                    skipped += 1
                    continue

                if not dry_run:
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
                created += 1

            if dry_run:
                transaction.set_rollback(True)

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}DEX data backfill: {created} assets with DEX data, "
                f"{skipped} skipped (no matching asset)."
            )
        )
