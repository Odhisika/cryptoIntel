from datetime import datetime, timezone

import pytest
import responses

from core.providers.base import ProviderError
from core.providers.github import GITHUB_BASE_URL, GitHubProvider, parse_owner_repo


@pytest.fixture
def provider():
    return GitHubProvider(max_retries=2)


# This payload shape was confirmed against a REAL live GitHub API response
# during this build (via /search/repositories, same schema) — see
# PHASE_6_NOTES.md for the verification transcript.
REPO_PAYLOAD = {
    "stargazers_count": 51278,
    "forks_count": 22092,
    "open_issues_count": 407,
    "archived": False,
    "pushed_at": "2026-08-08T13:14:05Z",
}

COMMIT_ACTIVITY_PAYLOAD = [
    {"days": [0, 1, 2, 0, 1, 0, 0], "total": 4, "week": 1000000},
    {"days": [1, 1, 1, 1, 1, 1, 1], "total": 7, "week": 1000604800},
    {"days": [2, 2, 2, 2, 2, 2, 2], "total": 14, "week": 1001209600},
    {"days": [3, 3, 3, 3, 3, 3, 3], "total": 21, "week": 1001814400},
]


def test_parse_owner_repo():
    assert parse_owner_repo("https://github.com/ethereum/go-ethereum") == ("ethereum", "go-ethereum")


def test_parse_owner_repo_handles_trailing_slash_and_git_suffix():
    assert parse_owner_repo("https://github.com/foo/bar.git") == ("foo", "bar")
    assert parse_owner_repo("https://github.com/foo/bar/") == ("foo", "bar")


def test_parse_owner_repo_returns_none_for_non_github_url():
    assert parse_owner_repo("https://gitlab.com/foo/bar") is None


@responses.activate
def test_fetch_repo_activity_typed_happy_path(provider):
    responses.add(
        responses.GET, f"{GITHUB_BASE_URL}/repos/ethereum/go-ethereum", json=REPO_PAYLOAD, status=200
    )
    responses.add(
        responses.GET, f"{GITHUB_BASE_URL}/repos/ethereum/go-ethereum/stats/commit_activity",
        json=COMMIT_ACTIVITY_PAYLOAD, status=200,
    )

    data = provider.fetch_repo_activity_typed("https://github.com/ethereum/go-ethereum")

    assert data.stars == 51278
    assert data.forks == 22092
    assert data.open_issues == 407
    assert data.is_archived is False
    assert data.pushed_at == datetime(2026, 8, 8, 13, 14, 5, tzinfo=timezone.utc)
    # Sum of last 4 weeks: 4+7+14+21 = 46
    assert data.commits_4w == 46


@responses.activate
def test_commit_activity_202_returns_none_not_error(provider):
    responses.add(
        responses.GET, f"{GITHUB_BASE_URL}/repos/foo/bar", json=REPO_PAYLOAD, status=200
    )
    responses.add(
        responses.GET, f"{GITHUB_BASE_URL}/repos/foo/bar/stats/commit_activity", json=[], status=202
    )

    data = provider.fetch_repo_activity_typed("https://github.com/foo/bar")
    assert data.commits_4w is None
    # Repo-level data should still be populated even though commits are unavailable.
    assert data.stars == 51278


@responses.activate
def test_archived_repo_is_flagged(provider):
    archived_payload = dict(REPO_PAYLOAD, archived=True)
    responses.add(responses.GET, f"{GITHUB_BASE_URL}/repos/foo/bar", json=archived_payload, status=200)
    responses.add(
        responses.GET, f"{GITHUB_BASE_URL}/repos/foo/bar/stats/commit_activity", json=[], status=202
    )
    data = provider.fetch_repo_activity_typed("https://github.com/foo/bar")
    assert data.is_archived is True


@responses.activate
def test_repo_not_found_raises_non_retryable(provider):
    responses.add(responses.GET, f"{GITHUB_BASE_URL}/repos/foo/nonexistent", status=404)
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_repo_activity_typed("https://github.com/foo/nonexistent")
    assert exc_info.value.retryable is False


@responses.activate
def test_rate_limit_exhausted_raises_retryable(provider):
    responses.add(
        responses.GET, f"{GITHUB_BASE_URL}/repos/foo/bar", status=403,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1234567890"},
    )
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_repo_activity_typed("https://github.com/foo/bar")
    assert exc_info.value.retryable is True


def test_unparseable_url_raises_non_retryable(provider):
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_repo_activity_typed("not-a-github-url")
    assert exc_info.value.retryable is False


@responses.activate
def test_fetch_repo_activity_dict_wrapper(provider):
    responses.add(responses.GET, f"{GITHUB_BASE_URL}/repos/foo/bar", json=REPO_PAYLOAD, status=200)
    responses.add(
        responses.GET, f"{GITHUB_BASE_URL}/repos/foo/bar/stats/commit_activity", json=[], status=202
    )
    result = provider.fetch_repo_activity("https://github.com/foo/bar")
    assert result["stars"] == 51278
    assert result["source"] == "github"
