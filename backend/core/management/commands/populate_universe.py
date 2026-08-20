"""
Populate the Asset table from CoinGecko's market-cap-filtered universe
(section 12 — market universe). Idempotent: re-running updates existing
Assets rather than duplicating them, keyed on external_ids.coingecko.

Usage:
    python manage.py populate_universe --min-market-cap 10000000 --max-market-cap 2000000000
"""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Asset
from core.providers import CoinGeckoProvider, ProviderError


class Command(BaseCommand):
    help = "Populate/refresh the Asset universe from CoinGecko within a market-cap band."

    def add_arguments(self, parser):
        parser.add_argument("--min-market-cap", type=str, default="10000000")
        parser.add_argument("--max-market-cap", type=str, default="2000000000")
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would change without writing."
        )

    def handle(self, *args, **options):
        min_mc = Decimal(options["min_market_cap"])
        max_mc = Decimal(options["max_market_cap"])
        dry_run = options["dry_run"]

        if min_mc >= max_mc:
            raise CommandError("--min-market-cap must be less than --max-market-cap")

        provider = CoinGeckoProvider()

        try:
            universe_ids = provider.list_universe(min_market_cap=min_mc, max_market_cap=max_mc)
        except ProviderError as exc:
            raise CommandError(f"Failed to fetch universe: {exc}")

        if not universe_ids:
            self.stdout.write(self.style.WARNING("No assets found in the given market-cap band."))
            return

        try:
            snapshots = provider.fetch_market_snapshot(universe_ids)
        except ProviderError as exc:
            raise CommandError(f"Failed to fetch snapshot details for universe: {exc}")

        existing = {
            a.external_ids.get("coingecko"): a
            for a in Asset.objects.filter(external_ids__has_key="coingecko")
        }

        created, updated = 0, 0

        with transaction.atomic():
            for snap in snapshots:
                asset = existing.get(snap.external_id)
                if asset:
                    changed = asset.symbol != snap.symbol or asset.name != snap.name
                    if changed and not dry_run:
                        asset.symbol = snap.symbol
                        asset.name = snap.name
                        asset.save(update_fields=["symbol", "name", "updated_at"])
                    if changed:
                        updated += 1
                else:
                    if not dry_run:
                        Asset.objects.create(
                            symbol=snap.symbol,
                            name=snap.name,
                            external_ids={"coingecko": snap.external_id},
                        )
                    created += 1

            if dry_run:
                # Never persist during a dry run.
                transaction.set_rollback(True)

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Universe sync: {len(universe_ids)} ids in band, "
                f"{created} to create, {updated} to update."
            )
        )
