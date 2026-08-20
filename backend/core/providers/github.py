"""
GitHub provider — developer activity (section 6 of the provider ABCs,
Phase 6 of the build).

LIVE VERIFICATION STATUS (2026-08-09): repo-level fields (stargazers_count,
forks_count, open_issues_count, pushed_at, archived) were confirmed
against a REAL live response from this environment (via GitHub's /search
endpoint, which shares the repository object schema with /repos/{owner}/
{repo} but draws from a separate rate-limit bucket) — see the exact
verification transcript in PHASE_6_NOTES.md. The `/stats/commit_activity`
endpoint was NOT live-verified this session because this sandbox's shared
egress IP had already exhausted its unauthenticated 60/hr "core" quota
before this chunk started; its shape is taken from GitHub's own stable,
long-published REST API docs instead. Recommend one live smoke-test call
to that specific endpoint once real network/auth is available (e.g.
Claude Code with a GITHUB_TOKEN), same discipline as every other
not-fully-live-verified provider in this codebase.

Auth: unauthenticated requests are limited to 60/hr (core) per IP — likely
too low to be useful for ingesting many assets. A GITHUB_TOKEN (free,
personal access token, no special scopes needed for public repo reads)
raises this to 5,000/hr. Strongly recommended for anything beyond a
handful of assets.
"""

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

from .base import DeveloperActivityProvider, ProviderError

GITHUB_BASE_URL = "https://api.github.com"

_REPO_URL_PATTERN = re.compile(r"github\.com/([^/]+)/([^/?#]+)")


def parse_owner_repo(repo_url: str) -> Optional[tuple[str, str]]:
    match = _REPO_URL_PATTERN.search(repo_url)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2).rstrip("/").removesuffix(".git")
    return owner, repo


@dataclass(frozen=True)
class RepoActivityData:
    owner: str
    repo: str
    stars: int
    forks: int
    open_issues: int
    is_archived: bool
    pushed_at: Optional[datetime]
    commits_4w: Optional[int]  # None = stats endpoint returned 202 (still computing) or was unavailable
    source: str


class GitHubProvider(DeveloperActivityProvider):
    name = "github"

    def __init__(self, *, token: Optional[str] = None, timeout: int = 15, max_retries: int = 3):
        self.token = token
        self.timeout = timeout
        self.max_retries = max_retries
        self._session = requests.Session()

    def _headers(self) -> dict:
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get(self, path: str) -> tuple[int, dict | list]:
        """Returns (status_code, json_body) — callers need the status
        code directly to handle GitHub's 202 (stats still computing)
        convention on the commit_activity endpoint, which isn't a normal
        error."""
        url = f"{GITHUB_BASE_URL}{path}"
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(url, headers=self._headers(), timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, 10))
                continue

            if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
                reset_at = resp.headers.get("X-RateLimit-Reset")
                raise ProviderError(
                    self.name, f"Rate limit exhausted, resets at unix {reset_at}", retryable=True
                )
            if resp.status_code == 404:
                raise ProviderError(self.name, f"Repo not found: {path}", retryable=False)
            if resp.status_code >= 500:
                last_error = ProviderError(self.name, f"HTTP {resp.status_code}", retryable=True)
                time.sleep(min(2 ** attempt, 10))
                continue
            if resp.status_code >= 400 and resp.status_code != 202:
                raise ProviderError(self.name, f"HTTP {resp.status_code}: {resp.text[:200]}", retryable=False)

            try:
                body = resp.json()
            except ValueError:
                body = {}
            return resp.status_code, body

        raise ProviderError(self.name, f"Exhausted {self.max_retries} retries: {last_error}", retryable=True)

    def fetch_repo_activity(self, repo_url: str) -> dict:
        """Satisfies the DeveloperActivityProvider ABC (dict return)."""
        data = self.fetch_repo_activity_typed(repo_url)
        return {
            "owner": data.owner, "repo": data.repo, "stars": data.stars, "forks": data.forks,
            "open_issues": data.open_issues, "is_archived": data.is_archived,
            "pushed_at": data.pushed_at, "commits_4w": data.commits_4w, "source": data.source,
        }

    def fetch_repo_activity_typed(self, repo_url: str) -> RepoActivityData:
        parsed = parse_owner_repo(repo_url)
        if parsed is None:
            raise ProviderError(self.name, f"Could not parse owner/repo from '{repo_url}'", retryable=False)
        owner, repo = parsed

        _, repo_payload = self._get(f"/repos/{owner}/{repo}")
        if not isinstance(repo_payload, dict):
            raise ProviderError(self.name, f"Unexpected /repos/{owner}/{repo} payload shape", retryable=False)

        pushed_at_raw = repo_payload.get("pushed_at")
        pushed_at = (
            datetime.fromisoformat(pushed_at_raw.replace("Z", "+00:00")) if pushed_at_raw else None
        )

        commits_4w = self._fetch_commits_4w(owner, repo)

        return RepoActivityData(
            owner=owner,
            repo=repo,
            stars=repo_payload.get("stargazers_count", 0) or 0,
            forks=repo_payload.get("forks_count", 0) or 0,
            open_issues=repo_payload.get("open_issues_count", 0) or 0,
            is_archived=bool(repo_payload.get("archived", False)),
            pushed_at=pushed_at,
            commits_4w=commits_4w,
            source=self.name,
        )

    def _fetch_commits_4w(self, owner: str, repo: str) -> Optional[int]:
        """GET /repos/{owner}/{repo}/stats/commit_activity — 52 weekly
        buckets. A 202 status means GitHub is still computing the stats
        cache for this repo (common on first request) — treated as "not
        available yet," not an error. Sums the last 4 weeks' commit
        counts as a recent-activity signal."""
        try:
            status, payload = self._get(f"/repos/{owner}/{repo}/stats/commit_activity")
        except ProviderError:
            return None

        if status == 202 or not isinstance(payload, list) or not payload:
            return None

        last_4_weeks = payload[-4:]
        try:
            return sum(week.get("total", 0) for week in last_4_weeks)
        except (AttributeError, TypeError):
            return None
