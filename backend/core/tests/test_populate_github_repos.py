from io import StringIO

import pytest
import responses
from django.core.management import call_command

from core.models import Asset
from core.providers.coingecko import COINGECKO_BASE_URL, CoinGeckoProvider

pytestmark = pytest.mark.django_db


@responses.activate
def test_fetch_github_repos_parses_links():
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/ethereum",
        json={"id": "ethereum", "links": {"repos_url": {"github": ["https://github.com/ethereum/go-ethereum"]}}},
        status=200,
    )
    provider = CoinGeckoProvider()
    repos = provider.fetch_github_repos("ethereum")
    assert repos == ["https://github.com/ethereum/go-ethereum"]


@responses.activate
def test_fetch_github_repos_empty_when_no_links():
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/some-coin", json={"id": "some-coin"}, status=200
    )
    provider = CoinGeckoProvider()
    assert provider.fetch_github_repos("some-coin") == []


@responses.activate
def test_populate_github_repos_sets_primary_repo():
    Asset.objects.create(symbol="eth", name="Ethereum", external_ids={"coingecko": "ethereum"})
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/ethereum",
        json={"id": "ethereum", "links": {"repos_url": {"github": [
            "https://github.com/ethereum/go-ethereum", "https://github.com/ethereum/solidity"
        ]}}},
        status=200,
    )
    out = StringIO()
    call_command("populate_github_repos", stdout=out)

    asset = Asset.objects.get()
    assert asset.github_repo_url == "https://github.com/ethereum/go-ethereum"
    assert "1 repo URLs found" in out.getvalue()


@responses.activate
def test_populate_github_repos_skips_already_populated_without_force():
    Asset.objects.create(
        symbol="eth", name="Ethereum", external_ids={"coingecko": "ethereum"},
        github_repo_url="https://github.com/existing/repo",
    )
    call_command("populate_github_repos")
    assert len(responses.calls) == 0


@responses.activate
def test_populate_github_repos_dry_run_does_not_write():
    Asset.objects.create(symbol="eth", name="Ethereum", external_ids={"coingecko": "ethereum"})
    responses.add(
        responses.GET, f"{COINGECKO_BASE_URL}/coins/ethereum",
        json={"id": "ethereum", "links": {"repos_url": {"github": ["https://github.com/ethereum/go-ethereum"]}}},
        status=200,
    )
    call_command("populate_github_repos", "--dry-run")
    asset = Asset.objects.get()
    assert asset.github_repo_url is None
