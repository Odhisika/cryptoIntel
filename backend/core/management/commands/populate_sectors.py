"""
Populate Asset.sector by fetching CoinGecko's category list and mapping it
through core.scoring.sectors.classify_sector. Idempotent — re-running
only touches assets whose sector is currently unset, unless --force is
given (useful after PRIORITY_RULES changes, to reclassify everything).

Usage:
    python manage.py populate_sectors
    python manage.py populate_sectors --force
    python manage.py populate_sectors --limit 50
"""

from django.core.management.base import BaseCommand, CommandError

from core.models import Asset
from core.providers.base import ProviderError
from core.providers.coingecko import CoinGeckoProvider
from core.scoring.sectors import classify_sector


class Command(BaseCommand):
    help = "Populate Asset.sector from CoinGecko categories."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument(
            "--force", action="store_true", help="Reclassify assets that already have a sector."
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        assets = Asset.objects.filter(is_active=True, external_ids__has_key="coingecko")
        if not options["force"]:
            assets = assets.filter(sector__isnull=True)
        assets = assets.order_by("symbol")
        if options["limit"]:
            assets = assets[: options["limit"]]

        provider = CoinGeckoProvider()
        classified, unclassified, failed = 0, 0, 0

        for asset in assets:
            coingecko_id = asset.external_ids["coingecko"]
            try:
                categories = provider.fetch_categories(coingecko_id)
            except ProviderError as exc:
                self.stderr.write(f"Failed to fetch categories for {asset.symbol} ({coingecko_id}): {exc}")
                failed += 1
                continue

            sector = classify_sector(categories)
            if not dry_run:
                asset.sector = sector
                asset.raw_categories = categories
                asset.save(update_fields=["sector", "raw_categories", "updated_at"])

            if sector:
                classified += 1
            else:
                unclassified += 1

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}{classified} classified, {unclassified} had no matching sector "
                f"(categories didn't match PRIORITY_RULES), {failed} failed to fetch."
            )
        )
