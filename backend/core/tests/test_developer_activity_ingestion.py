import pytest
import responses

from core.models import Asset, DataIngestionJob, DeveloperActivitySnapshot
from core.providers.github import GITHUB_BASE_URL
from core.tasks.developer_activity_ingestion import ingest_developer_activity

pytestmark = pytest.mark.django_db

REPO_PAYLOAD = {
    "stargazers_count": 1000, "forks_count": 200, "open_issues_count": 10,
    "archived": False, "pushed_at": "2026-08-08T13:14:05Z",
}


def make_asset_with_repo(symbol="uni", repo_url="https://github.com/foo/bar"):
    return Asset.objects.create(symbol=symbol, name=symbol.upper(), github_repo_url=repo_url)


def test_no_assets_with_repo_succeeds_with_zero_attempted():
    result = ingest_developer_activity()
    assert result == {"attempted": 0, "succeeded": 0}


@responses.activate
def test_ingests_developer_activity_snapshot():
    make_asset_with_repo()
    responses.add(responses.GET, f"{GITHUB_BASE_URL}/repos/foo/bar", json=REPO_PAYLOAD, status=200)
    responses.add(
        responses.GET, f"{GITHUB_BASE_URL}/repos/foo/bar/stats/commit_activity", json=[], status=202
    )

    result = ingest_developer_activity()

    assert result["succeeded"] == 1
    assert DeveloperActivitySnapshot.objects.count() == 1
    snap = DeveloperActivitySnapshot.objects.get()
    assert snap.stars == 1000
    assert snap.commits_4w is None


@responses.activate
def test_one_repo_failure_does_not_block_others():
    make_asset_with_repo("aaa", "https://github.com/foo/bar")
    make_asset_with_repo("bbb", "https://github.com/foo/gone")
    responses.add(responses.GET, f"{GITHUB_BASE_URL}/repos/foo/bar", json=REPO_PAYLOAD, status=200)
    responses.add(
        responses.GET, f"{GITHUB_BASE_URL}/repos/foo/bar/stats/commit_activity", json=[], status=202
    )
    responses.add(responses.GET, f"{GITHUB_BASE_URL}/repos/foo/gone", status=404)

    result = ingest_developer_activity()

    assert result["succeeded"] == 1
    assert "bbb" in result["failed"]
    job = DataIngestionJob.objects.get()
    assert job.status == DataIngestionJob.Status.PARTIAL


def test_asset_without_repo_url_is_excluded():
    Asset.objects.create(symbol="norepo", name="No Repo")
    result = ingest_developer_activity()
    assert result["attempted"] == 0
