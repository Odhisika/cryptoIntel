"""
Seed the initial market regime snapshot from Binance data.

Usage:
    python manage.py populate_market_regime
    python manage.py populate_market_regime --dry-run
"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import CommandError

from core.models import MarketRegimeSnapshot
from core.providers import BinanceProvider, ProviderError
from core.tasks.regime_ingestion import _classify_regime, _compute_7d_return


class Command(BaseCommand):
    help = "Seed market regime snapshot from Binance BTC/ETH data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would be written without writing."
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        provider = BinanceProvider()
        now = datetime.now(timezone.utc)

        self.stdout.write("Fetching BTC/ETH data from Binance...")

        try:
            btc_ticker = provider.fetch_24h_ticker("BTCUSDT")
            eth_ticker = provider.fetch_24h_ticker("ETHUSDT")
            btc_candles = provider.fetch_candles("BTCUSDT", interval="1d", limit=100)
            eth_candles = provider.fetch_candles("ETHUSDT", interval="1d", limit=100)
            all_tickers = provider.fetch_all_usdt_tickers()
        except ProviderError as exc:
            raise CommandError(f"Failed to fetch Binance data: {exc}")

        btc_50dma = provider.compute_daily_moving_average(btc_candles, 50)
        btc_above_50dma = btc_ticker.price_usd > btc_50dma if btc_50dma else None

        btc_change_7d = _compute_7d_return(btc_candles)
        eth_change_7d = _compute_7d_return(eth_candles)

        eth_btc_ratio = None
        eth_btc_change_7d = None
        if btc_ticker.price_usd > 0:
            eth_btc_ratio = (eth_ticker.price_usd / btc_ticker.price_usd).quantize(
                Decimal("0.0000000001"), rounding=ROUND_HALF_UP
            )
            if len(btc_candles) >= 8 and len(eth_candles) >= 8:
                btc_7d_ago = btc_candles[-8].close
                eth_7d_ago = eth_candles[-8].close
                if btc_7d_ago > 0 and eth_7d_ago > 0:
                    ratio_7d_ago = eth_7d_ago / btc_7d_ago
                    if ratio_7d_ago > 0:
                        eth_btc_change_7d = (
                            ((eth_btc_ratio - ratio_7d_ago) / ratio_7d_ago) * Decimal("100")
                        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        btc_dominance = provider.compute_btc_volume_dominance(all_tickers)
        total_usdt_volume = sum(
            (t.quote_volume_24h_usd or Decimal("0") for t in all_tickers),
            Decimal("0"),
        )

        regime, confidence = _classify_regime(
            btc_above_50dma=btc_above_50dma,
            btc_change_7d_pct=btc_change_7d,
            eth_btc_change_7d_pct=eth_btc_change_7d,
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("[DRY RUN] Would create:"))
        else:
            MarketRegimeSnapshot.objects.create(
                btc_price_usd=btc_ticker.price_usd,
                eth_price_usd=eth_ticker.price_usd,
                btc_change_7d_pct=btc_change_7d,
                eth_change_7d_pct=eth_change_7d,
                btc_above_50dma=btc_above_50dma,
                btc_50dma_value=btc_50dma,
                eth_btc_ratio=eth_btc_ratio,
                eth_btc_change_7d_pct=eth_btc_change_7d,
                btc_volume_dominance_pct=btc_dominance,
                total_usdt_volume_24h_usd=total_usdt_volume if total_usdt_volume > 0 else None,
                regime=regime,
                regime_confidence=confidence,
                source="binance",
                observed_at=now,
            )
            self.stdout.write(self.style.SUCCESS("Market regime snapshot created."))

        self.stdout.write(f"  BTC: ${btc_ticker.price_usd:,.2f} (7D: {btc_change_7d}%)")
        self.stdout.write(f"  ETH: ${eth_ticker.price_usd:,.2f} (7D: {eth_change_7d}%)")
        self.stdout.write(f"  ETH/BTC: {eth_btc_ratio} (7D change: {eth_btc_change_7d}%)")
        self.stdout.write(f"  BTC 50DMA: ${btc_50dma:,.2f} (above: {btc_above_50dma})")
        self.stdout.write(f"  BTC volume dominance: {btc_dominance}%")
        self.stdout.write(f"  Regime: {regime} (confidence: {confidence})")
