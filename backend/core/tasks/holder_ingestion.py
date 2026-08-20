import logging
from datetime import datetime, timezone

from celery import shared_task

from core.models import ContractAddress, DataIngestionJob, HolderSnapshot
from core.providers.base import ProviderError
from core.providers.onchain import ASSET_PLATFORM_TO_ONCHAIN_NETWORK, CoinGeckoOnChainProvider

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def ingest_holder_snapshots(self):
    """Fetch current holder count + top-10 concentration for every
    ContractAddress on a chain we have a network mapping for. Unmapped
    chains are skipped up front (not counted as failures — they're a
    known gap, not an error) and reported separately."""

    job = DataIngestionJob.objects.create(provider="coingecko_onchain", job_type="holder_snapshot")

    all_addresses = list(ContractAddress.objects.select_related("blockchain", "asset"))
    mapped = [a for a in all_addresses if a.blockchain.slug in ASSET_PLATFORM_TO_ONCHAIN_NETWORK]
    unmapped_count = len(all_addresses) - len(mapped)

    job.assets_attempted = len(mapped)
    job.save(update_fields=["assets_attempted"])

    if not mapped:
        job.status = DataIngestionJob.Status.SUCCESS
        job.finished_at = datetime.now(timezone.utc)
        job.error_summary = f"No contract addresses on a mapped chain ({unmapped_count} unmapped)."
        job.save()
        return {"attempted": 0, "succeeded": 0, "unmapped_chains": unmapped_count}

    provider = CoinGeckoOnChainProvider()
    succeeded, no_data, failed = 0, 0, []

    for contract in mapped:
        try:
            data = provider.fetch_holder_data(contract.blockchain.slug, contract.address)
        except ProviderError as exc:
            logger.warning(
                "Holder fetch failed for asset=%s chain=%s: %s", contract.asset.symbol, contract.blockchain.slug, exc
            )
            failed.append(f"{contract.asset.symbol}/{contract.blockchain.slug}")
            continue

        if data.holder_count is None:
            # Real "no data available" from a Beta/coverage-limited
            # endpoint — not a failure, just nothing to store.
            no_data += 1
            continue

        HolderSnapshot.objects.update_or_create(
            contract_address=contract,
            source=data.source,
            observed_at=data.observed_at,
            defaults={
                "holder_count": data.holder_count,
                "top_10_concentration_pct": data.top_10_concentration_pct,
            },
        )
        succeeded += 1

    job.assets_succeeded = succeeded
    job.status = (
        DataIngestionJob.Status.SUCCESS if succeeded == len(mapped) else DataIngestionJob.Status.PARTIAL
    )
    job.error_summary = (
        f"{unmapped_count} unmapped-chain addresses skipped; {no_data} had no holders data available; "
        f"failed: {', '.join(failed[:20])}"
    )
    job.finished_at = datetime.now(timezone.utc)
    job.save()

    return {
        "attempted": len(mapped),
        "succeeded": succeeded,
        "no_data": no_data,
        "unmapped_chains": unmapped_count,
        "failed": failed,
    }
