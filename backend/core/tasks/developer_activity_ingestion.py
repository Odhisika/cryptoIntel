import logging
from datetime import datetime, timezone

from celery import shared_task
from django.conf import settings

from core.models import Asset, DataIngestionJob, DeveloperActivitySnapshot
from core.providers.base import ProviderError
from core.providers.github import GitHubProvider

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def ingest_developer_activity(self):
    """Fetch current repo stats + recent commit activity for every active
    Asset with a known github_repo_url. One repo failing (renamed,
    deleted, private) never blocks the rest."""

    job = DataIngestionJob.objects.create(provider="github", job_type="developer_activity_snapshot")
    assets = list(Asset.objects.filter(is_active=True, github_repo_url__isnull=False))
    job.assets_attempted = len(assets)
    job.save(update_fields=["assets_attempted"])

    if not assets:
        job.status = DataIngestionJob.Status.SUCCESS
        job.finished_at = datetime.now(timezone.utc)
        job.error_summary = "No assets with a known GitHub repo to ingest."
        job.save()
        return {"attempted": 0, "succeeded": 0}

    provider = GitHubProvider(token=settings.GITHUB_TOKEN or None)
    succeeded, failed = 0, []
    now = datetime.now(timezone.utc)

    for asset in assets:
        try:
            data = provider.fetch_repo_activity_typed(asset.github_repo_url)
        except ProviderError as exc:
            logger.warning("GitHub fetch failed for asset=%s: %s", asset.symbol, exc)
            failed.append(asset.symbol)
            continue

        DeveloperActivitySnapshot.objects.update_or_create(
            asset=asset, source=data.source, observed_at=now,
            defaults={
                "stars": data.stars, "forks": data.forks, "open_issues": data.open_issues,
                "is_archived": data.is_archived, "repo_pushed_at": data.pushed_at,
                "commits_4w": data.commits_4w,
            },
        )
        succeeded += 1

    job.assets_succeeded = succeeded
    job.status = (
        DataIngestionJob.Status.SUCCESS if succeeded == len(assets) else DataIngestionJob.Status.PARTIAL
    )
    if failed:
        job.error_summary = f"Failed: {', '.join(failed[:20])}"
    job.finished_at = datetime.now(timezone.utc)
    job.save()

    return {"attempted": len(assets), "succeeded": succeeded, "failed": failed}
