import logging
from datetime import datetime, timezone

from celery import shared_task

from core.models import DataIngestionJob, FeeSnapshot, Protocol, RevenueSnapshot
from core.providers.base import ProviderError
from core.providers.defillama import DefiLlamaProvider

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def ingest_fee_revenue_snapshots(self):
    """Fetch current fees (and revenue, where the protocol takes a cut)
    for every active Protocol. A protocol with no dailyRevenue in the
    payload gets a FeeSnapshot but NOT a RevenueSnapshot — that's the
    correct representation of "takes no fee cut," not a failure."""

    job = DataIngestionJob.objects.create(provider="defillama", job_type="fee_revenue_snapshot")
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
    succeeded, failed_slugs, revenue_recorded = 0, [], 0

    for protocol in protocols:
        try:
            data = provider.fetch_protocol_fees(protocol.slug)
        except ProviderError as exc:
            logger.warning("Fees fetch failed for protocol=%s: %s", protocol.slug, exc)
            failed_slugs.append(protocol.slug)
            continue

        FeeSnapshot.objects.update_or_create(
            protocol=protocol,
            source=data.source,
            observed_at=data.observed_at,
            defaults={
                "fees_24h_usd": data.fees_24h_usd,
                "fees_7d_usd": data.fees_7d_usd,
                "fees_30d_usd": data.fees_30d_usd,
            },
        )

        if data.revenue_24h_usd is not None:
            RevenueSnapshot.objects.update_or_create(
                protocol=protocol,
                source=data.source,
                observed_at=data.observed_at,
                defaults={"revenue_24h_usd": data.revenue_24h_usd},
            )
            revenue_recorded += 1

        succeeded += 1

    job.assets_succeeded = succeeded
    job.status = (
        DataIngestionJob.Status.SUCCESS if succeeded == len(protocols) else DataIngestionJob.Status.PARTIAL
    )
    if failed_slugs:
        job.error_summary = f"Failed slugs: {', '.join(failed_slugs[:20])}"
    job.finished_at = datetime.now(timezone.utc)
    job.save()

    return {
        "attempted": len(protocols),
        "succeeded": succeeded,
        "revenue_recorded": revenue_recorded,
        "failed": failed_slugs,
    }
