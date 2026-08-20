"""
Mock providers.

Per the master spec (section 62): when blocked by an external API key or an
unverified/unlicensed provider, implement the abstraction + a mock, document
what's missing, and keep moving — never fabricate data in a way that could
be mistaken for a real value. Every mock here returns an obviously-fake,
clearly-flagged payload so it can never silently leak into a real score.
"""

from .base import (
    DefiDataProvider,
    DeveloperActivityProvider,
    NewsProvider,
    OnChainProvider,
    SocialDataProvider,
    TokenomicsProvider,
)


class MockDefiDataProvider(DefiDataProvider):
    name = "mock_defi"

    def fetch_protocol_metrics(self, protocol_id: str) -> dict:
        return {"protocol_id": protocol_id, "tvl_usd": None, "mocked": True,
                "note": "No real DeFi provider wired up yet — needs DefiLlama or equivalent (Phase 3)."}


class MockOnChainProvider(OnChainProvider):
    name = "mock_onchain"

    def fetch_holder_distribution(self, contract_address: str, chain: str) -> dict:
        return {"contract_address": contract_address, "chain": chain, "holders": None,
                "mocked": True, "note": "No on-chain provider wired up yet (Phase 5)."}


class MockTokenomicsProvider(TokenomicsProvider):
    name = "mock_tokenomics"

    def fetch_unlock_schedule(self, external_id: str) -> dict:
        return {"external_id": external_id, "unlocks": [], "mocked": True,
                "note": "No tokenomics provider wired up yet (Phase 4)."}


class MockDeveloperActivityProvider(DeveloperActivityProvider):
    name = "mock_dev_activity"

    def fetch_repo_activity(self, repo_url: str) -> dict:
        return {"repo_url": repo_url, "commits_30d": None, "mocked": True,
                "note": "GitHub provider not wired up yet (Phase 6)."}


class MockSocialDataProvider(SocialDataProvider):
    name = "mock_social"

    def fetch_social_metrics(self, external_id: str) -> dict:
        return {"external_id": external_id, "mocked": True,
                "note": "No licensed social provider wired up yet (Phase 8)."}


class MockNewsProvider(NewsProvider):
    name = "mock_news"

    def fetch_recent_events(self, external_id: str) -> list[dict]:
        return []
