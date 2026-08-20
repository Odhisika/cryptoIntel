from django.core.management.base import BaseCommand, CommandError

from core.models import Asset
from core.scoring.comparables import find_comparables


class Command(BaseCommand):
    help = "Print the comparable-project analysis for a given asset symbol."

    def add_arguments(self, parser):
        parser.add_argument("symbol")

    def handle(self, *args, **options):
        symbol = options["symbol"].lower()
        asset = Asset.objects.filter(symbol__iexact=symbol).first()
        if asset is None:
            raise CommandError(f"No asset found with symbol '{symbol}'")

        result = find_comparables(asset)
        if result is None:
            reason = "no sector assigned" if not asset.sector else "no market cap data"
            self.stdout.write(self.style.WARNING(f"Cannot compute comparables for {asset.symbol}: {reason}."))
            return

        self.stdout.write(f"Sector: {result.sector}")
        self.stdout.write(f"Candidate market cap: ${result.candidate_market_cap_usd:,.0f}")
        self.stdout.write(f"Peer count (same sector, within 3x-0.33x market cap): {result.peer_count}")

        if result.peer_median_market_cap_usd:
            vs_peer = result.market_cap_vs_peer_median_pct()
            self.stdout.write(f"Peer median market cap: ${result.peer_median_market_cap_usd:,.0f}")
            self.stdout.write(f"Candidate vs peer median: {vs_peer:+.1f}%")
        else:
            self.stdout.write("No peers found in this sector/bracket.")

        for label, candidate_val, peer_val in [
            ("TVL multiple (MC/TVL)", result.candidate_tvl_multiple, result.peer_median_tvl_multiple),
            ("Revenue multiple (MC/Revenue)", result.candidate_revenue_multiple, result.peer_median_revenue_multiple),
        ]:
            if candidate_val is not None or peer_val is not None:
                cand_str = f"{candidate_val:.1f}x" if candidate_val is not None else "insufficient_data"
                peer_str = f"{peer_val:.1f}x" if peer_val is not None else "insufficient_data"
                self.stdout.write(f"{label}: candidate={cand_str}, peer median={peer_str}")

        self.stdout.write(f"User multiple: {result.peer_user_multiple_note}")
