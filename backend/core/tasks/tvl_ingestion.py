import logging
from datetime import datetime, timezone

from celery import shared_task
from django.db import transaction

from core.models import DataIngestionJob, Protocol, TVLSnapshot
from core.providers.base import ProviderError
from core.providers.defillama import DefiLlamaProvider

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def ingest_tvl_snapshots(self):
    """Fetch current TVL for every active Protocol and store a new
    TVLSnapshot, without overwriting history. One protocol failing
    (e.g. DefiLlama slug renamed/removed) never blocks the rest."""

    job = DataIngestionJob.objects.create(provider="defillama", job_type="tvl_snapshot")
    protocols = list(Protocol.objects.filter(is_active=True))
    job.assets_attempted = len(protocols)
    job.save(update_fields=["assets_attempted"])

    if not protocols:
        job.status = DataIngestionJob.Status.SUCCESS
        job.finished_at = datetime.now(timezone.utc)
        job.error_summary = "No active protocols to ingest."
        job.save()
        return {"attempted": 0, "succeeded": 0}

    provider = DefiLlamaProvider()
    succeeded, failed_slugs = 0, []

    for protocol in protocols:
        try:
            data = provider.fetch_protocol_tvl(protocol.slug)
        except ProviderError as exc:
            logger.warning("TVL fetch failed for protocol=%s: %s", protocol.slug, exc)
            failed_slugs.append(protocol.slug)
            continue

        with transaction.atomic():
            TVLSnapshot.objects.update_or_create(
                protocol=protocol,
                source=data.source,
                observed_at=data.observed_at,
                defaults={
                    "tvl_usd": data.tvl_usd,
                    "change_1d_pct": data.change_1d_pct,
                    "change_7d_pct": data.change_7d_pct,
                },
            )
        succeeded += 1

    job.assets_succeeded = succeeded
    job.status = (
        DataIngestionJob.Status.SUCCESS if succeeded == len(protocols) else DataIngestionJob.Status.PARTIAL
    )
    if failed_slugs:
        job.error_summary = f"Failed slugs: {', '.join(failed_slugs[:20])}"
    job.finished_at = datetime.now(timezone.utc)
    job.save()

    return {"attempted": len(protocols), "succeeded": succeeded, "failed": failed_slugs}
