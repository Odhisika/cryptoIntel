"""
Populate/refresh the Protocol table from DefiLlama's /protocols list,
matching each protocol to an existing Asset via gecko_id <-> Asset's
coingecko external_id. This is the identity-resolution step (section 11)
for DeFi fundamentals — a protocol only gets scored into Undervaluation/
10X Potential's fundamentals factors once it's linked to an Asset that's
already in our universe (populate_universe must run first).

Usage:
    python manage.py populate_protocols
    python manage.py populate_protocols --dry-run
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Asset, Protocol
from core.providers.defillama import DefiLlamaProvider
from core.providers.base import ProviderError


class Command(BaseCommand):
    help = "Populate/refresh Protocol rows from DefiLlama, matched to existing Assets via gecko_id."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        provider = DefiLlamaProvider()

        try:
            listings = provider.list_protocols()
        except ProviderError as exc:
            raise CommandError(f"Failed to fetch protocol list: {exc}")

        assets_by_coingecko_id = {
            a.external_ids.get("coingecko"): a
            for a in Asset.objects.filter(external_ids__has_key="coingecko")
        }

        matched, unmatched, created, updated = 0, 0, 0, 0

        with transaction.atomic():
            for listing in listings:
                asset = assets_by_coingecko_id.get(listing.gecko_id) if listing.gecko_id else None
                if asset is None:
                    unmatched += 1
                    continue
                matched += 1

                existing = Protocol.objects.filter(slug=listing.slug).first()
                if existing:
                    changed = (
                        existing.asset_id != asset.id
                        or existing.name != listing.name
                        or existing.category != (listing.category or "")
                        or existing.chains != listing.chains
                    )
                    if changed and not dry_run:
                        existing.asset = asset
                        existing.name = listing.name
                        existing.category = listing.category or ""
                        existing.chains = listing.chains
                        existing.save(update_fields=["asset", "name", "category", "chains", "updated_at"])
                    if changed:
                        updated += 1
                else:
                    if not dry_run:
                        Protocol.objects.create(
                            asset=asset,
                            slug=listing.slug,
                            name=listing.name,
                            category=listing.category or "",
                            chains=listing.chains,
                        )
                    created += 1

            if dry_run:
                transaction.set_rollback(True)

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}{len(listings)} protocols seen, {matched} matched to an existing Asset "
                f"({unmatched} unmatched — no Asset with that gecko_id), {created} to create, {updated} to update."
            )
        )
