"""
Populate ContractAddress (and Blockchain) rows for existing Assets, using
CoinGecko's /coins/{id} `platforms` field. Needed before on-chain holder
data can be looked up — that requires a chain + contract address per
asset, which nothing before Phase 3.2 has ingested.

Usage:
    python manage.py populate_contract_addresses
    python manage.py populate_contract_addresses --limit 50
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Asset, Blockchain, ContractAddress
from core.providers.base import ProviderError
from core.providers.coingecko import CoinGeckoProvider


class Command(BaseCommand):
    help = "Populate ContractAddress rows for Assets from CoinGecko's platforms data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=None,
            help="Only process this many assets (useful for staying under rate limits on a first run).",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        assets = Asset.objects.filter(is_active=True, external_ids__has_key="coingecko").order_by("symbol")
        if options["limit"]:
            assets = assets[: options["limit"]]

        provider = CoinGeckoProvider()
        created, skipped_existing, failed = 0, 0, 0

        for asset in assets:
            if asset.contract_addresses.exists():
                skipped_existing += 1
                continue

            coingecko_id = asset.external_ids["coingecko"]
            try:
                platforms = provider.fetch_platforms(coingecko_id)
            except ProviderError as exc:
                self.stderr.write(f"Failed to fetch platforms for {asset.symbol} ({coingecko_id}): {exc}")
                failed += 1
                continue

            if not platforms:
                continue

            with transaction.atomic():
                for platform_id, address in platforms.items():
                    if dry_run:
                        created += 1
                        continue
                    blockchain, _ = Blockchain.objects.get_or_create(
                        slug=platform_id, defaults={"name": platform_id.replace("-", " ").title()}
                    )
                    ContractAddress.objects.get_or_create(
                        asset=asset, blockchain=blockchain, address=address
                    )
                    created += 1

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}{created} contract addresses created, {skipped_existing} assets already had "
                f"addresses, {failed} failed to fetch."
            )
        )
