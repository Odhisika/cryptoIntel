"""
Manually add a curated Catalyst entry. This is deliberately a manual,
one-at-a-time command rather than a bulk-import or scraping tool — per
section 23, catalysts must never be fabricated or inferred from rumor,
and no free/licensable automated events feed was found (Phase 8
research, see docs/DATA_LICENSING.md). Every entry requires a real
source URL and an explicit confidence level from whoever is curating it.

Usage:
    python manage.py add_catalyst BTC \
        --title "ETF Decision Deadline" \
        --description "SEC final deadline to rule on spot ETF applications." \
        --type governance_event \
        --date 2026-12-01 \
        --source "https://www.sec.gov/..." \
        --confidence confirmed \
        --impact high \
        --added-by "jane@example.com"
"""

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from core.models import Asset, Catalyst


class Command(BaseCommand):
    help = "Manually add a curated, sourced catalyst for an asset."

    def add_arguments(self, parser):
        parser.add_argument("symbol", help="Asset symbol, e.g. BTC")
        parser.add_argument("--title", required=True)
        parser.add_argument("--description", required=True)
        parser.add_argument("--type", required=True, choices=[c[0] for c in Catalyst.CatalystType.choices])
        parser.add_argument("--date", required=True, help="YYYY-MM-DD")
        parser.add_argument("--source", required=True, help="URL to the original source — required, not optional.")
        parser.add_argument("--confidence", required=True, choices=[c[0] for c in Catalyst.Confidence.choices])
        parser.add_argument("--impact", required=True, choices=["low", "medium", "high"])
        parser.add_argument("--status", default=Catalyst.Status.UPCOMING, choices=[c[0] for c in Catalyst.Status.choices])
        parser.add_argument("--added-by", default="", help="Who curated this entry, for audit.")

    def handle(self, *args, **options):
        asset = Asset.objects.filter(symbol__iexact=options["symbol"]).first()
        if asset is None:
            raise CommandError(f"No asset found with symbol '{options['symbol']}'")

        try:
            event_date = datetime.strptime(options["date"], "%Y-%m-%d").date()
        except ValueError:
            raise CommandError("--date must be in YYYY-MM-DD format")

        if not options["source"].startswith(("http://", "https://")):
            raise CommandError("--source must be a URL — catalysts require a real, checkable source.")

        catalyst = Catalyst.objects.create(
            asset=asset,
            title=options["title"],
            description=options["description"],
            catalyst_type=options["type"],
            event_date=event_date,
            source_url=options["source"],
            confidence=options["confidence"],
            impact_estimate=options["impact"],
            status=options["status"],
            added_by=options["added_by"],
        )

        self.stdout.write(self.style.SUCCESS(f"Added catalyst: {catalyst}"))
