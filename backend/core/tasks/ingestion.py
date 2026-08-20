import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from celery import shared_task
from django.conf import settings
from django.db import transaction

from core.models import Asset, DataIngestionJob, DataQualityIssue, MarketSnapshot
from core.providers import CoinGeckoProvider, ProviderError

logger = logging.getLogger(__name__)

# Sanity bounds for data-quality checks (section 10). These are deliberately
# generous — the goal is catching obviously-broken data (a $0 price on a
# live asset, a 100x price jump between consecutive snapshots), not making
# trading judgments.
MAX_PLAUSIBLE_PRICE_JUMP_RATIO = Decimal("10")  # 10x move between snapshots flags a review
STALE_THRESHOLD = timedelta(hours=6)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def ingest_market_snapshots(self):
    """Fetch current market data for every active Asset with a coingecko id
    and store a new MarketSnapshot for each, without overwriting history."""

    job = DataIngestionJob.objects.create(provider="coingecko", job_type="market_snapshot")

    assets = list(Asset.objects.filter(is_active=True, external_ids__has_key="coingecko"))
    id_to_asset = {a.external_ids["coingecko"]: a for a in assets}
    job.assets_attempted = len(assets)
    job.save(update_fields=["assets_attempted"])

    if not assets:
        job.status = DataIngestionJob.Status.SUCCESS
        job.finished_at = datetime.now(timezone.utc)
        job.error_summary = "No assets with a coingecko external_id to ingest."
        job.save()
        return {"attempted": 0, "succeeded": 0}

    provider = CoinGeckoProvider(api_key=settings.COINGECKO_API_KEY or None)

    try:
        snapshots = provider.fetch_market_snapshot(list(id_to_asset.keys()))
    except ProviderError as exc:
        job.status = DataIngestionJob.Status.FAILED
        job.error_summary = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        job.save()
        if exc.retryable:
            raise self.retry(exc=exc)
        logger.error("Non-retryable provider error: %s", exc)
        return {"attempted": len(assets), "succeeded": 0, "error": str(exc)}

    succeeded = 0
    with transaction.atomic():
        for snap in snapshots:
            asset = id_to_asset.get(snap.external_id)
            if asset is None:
                continue

            _flag_anomalies_if_any(asset, snap, job)

            MarketSnapshot.objects.update_or_create(
                asset=asset,
                source=snap.source,
                observed_at=snap.observed_at,
                defaults={
                    "price_usd": snap.price_usd,
                    "market_cap_usd": snap.market_cap_usd,
                    "fully_diluted_valuation_usd": snap.fully_diluted_valuation_usd,
                    "volume_24h_usd": snap.volume_24h_usd,
                    "circulating_supply": snap.circulating_supply,
                    "total_supply": snap.total_supply,
                    "max_supply": snap.max_supply,
                },
            )
            succeeded += 1

    job.assets_succeeded = succeeded
    job.status = (
        DataIngestionJob.Status.SUCCESS if succeeded == len(assets) else DataIngestionJob.Status.PARTIAL
    )
    job.finished_at = datetime.now(timezone.utc)
    job.save()

    return {"attempted": len(assets), "succeeded": succeeded}


def _flag_anomalies_if_any(asset: Asset, snapshot, job: DataIngestionJob) -> None:
    """Basic data-quality checks — extend per section 10 as more checks are
    identified. Flags, never silently drops or "corrects" data."""

    if snapshot.price_usd <= 0:
        DataQualityIssue.objects.create(
            asset=asset,
            ingestion_job=job,
            issue_type="impossible_price",
            severity=DataQualityIssue.Severity.CRITICAL,
            details={"price_usd": str(snapshot.price_usd)},
        )
        return

    previous = (
        MarketSnapshot.objects.filter(asset=asset, source=snapshot.source)
        .order_by("-observed_at")
        .first()
    )
    if previous and previous.price_usd > 0:
        ratio = snapshot.price_usd / previous.price_usd
        if ratio >= MAX_PLAUSIBLE_PRICE_JUMP_RATIO or ratio <= (1 / MAX_PLAUSIBLE_PRICE_JUMP_RATIO):
            DataQualityIssue.objects.create(
                asset=asset,
                ingestion_job=job,
                issue_type="price_anomaly",
                severity=DataQualityIssue.Severity.WARNING,
                details={
                    "previous_price": str(previous.price_usd),
                    "new_price": str(snapshot.price_usd),
                    "ratio": str(ratio),
                },
            )

        gap = snapshot.observed_at - previous.observed_at
        if gap > STALE_THRESHOLD:
            DataQualityIssue.objects.create(
                asset=asset,
                ingestion_job=job,
                issue_type="stale_data_gap",
                severity=DataQualityIssue.Severity.WARNING,
                details={
                    "previous_observed_at": previous.observed_at.isoformat(),
                    "new_observed_at": snapshot.observed_at.isoformat(),
                    "gap_hours": gap.total_seconds() / 3600,
                },
            )

    supply_fields = {
        "circulating_supply": (previous.circulating_supply if previous else None, snapshot.circulating_supply),
        "total_supply": (previous.total_supply if previous else None, snapshot.total_supply),
    }
    for field_name, (old_val, new_val) in supply_fields.items():
        if old_val and new_val and old_val > 0:
            supply_ratio = new_val / old_val
            if supply_ratio >= Decimal("1.5") or supply_ratio <= Decimal("0.5"):
                DataQualityIssue.objects.create(
                    asset=asset,
                    ingestion_job=job,
                    issue_type="sudden_supply_change",
                    severity=DataQualityIssue.Severity.CRITICAL,
                    details={
                        "field": field_name,
                        "previous_value": str(old_val),
                        "new_value": str(new_val),
                        "ratio": str(supply_ratio),
                    },
                )


def find_stale_assets(threshold: timedelta = STALE_THRESHOLD):
    """Assets whose most recent snapshot is older than `threshold`, or that
    have no snapshot at all. Used by monitoring/alerting, not by ingestion
    itself — ingestion flags gaps between consecutive snapshots; this flags
    assets that currently have no fresh data at all."""

    now = datetime.now(timezone.utc)
    stale = []
    for asset in Asset.objects.filter(is_active=True):
        latest = asset.market_snapshots.order_by("-observed_at").first()
        if latest is None or (now - latest.observed_at) > threshold:
            stale.append(asset)
    return stale
