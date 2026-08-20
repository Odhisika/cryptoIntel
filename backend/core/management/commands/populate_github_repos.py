"""
Populate Asset.github_repo_url from CoinGecko's links.repos_url.github
field. Picks the first listed repo as "primary" for multi-repo projects —
a simplification, not a claim about which repo matters most.

Usage:
    python manage.py populate_github_repos
    python manage.py populate_github_repos --force
"""

from django.core.management.base import BaseCommand

from core.models import Asset
from core.providers.base import ProviderError
from core.providers.coingecko import CoinGeckoProvider


class Command(BaseCommand):
    help = "Populate Asset.github_repo_url from CoinGecko's repos_url.github field."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--force", action="store_true", help="Overwrite assets that already have a repo URL.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        assets = Asset.objects.filter(is_active=True, external_ids__has_key="coingecko")
        if not options["force"]:
            assets = assets.filter(github_repo_url__isnull=True)
        assets = assets.order_by("symbol")
        if options["limit"]:
            assets = assets[: options["limit"]]

        provider = CoinGeckoProvider()
        found, no_repo, failed = 0, 0, 0

        for asset in assets:
            coingecko_id = asset.external_ids["coingecko"]
            try:
                repos = provider.fetch_github_repos(coingecko_id)
            except ProviderError as exc:
                self.stderr.write(f"Failed to fetch repos for {asset.symbol} ({coingecko_id}): {exc}")
                failed += 1
                continue

            if not repos:
                no_repo += 1
                continue

            if not dry_run:
                asset.github_repo_url = repos[0]
                asset.save(update_fields=["github_repo_url", "updated_at"])
            found += 1

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(f"{prefix}{found} repo URLs found, {no_repo} had none listed, {failed} failed.")
        )
