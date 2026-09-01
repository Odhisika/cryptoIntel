"""
Core schema v1.

Deliberately minimal — only what Phase 1 (market data foundation) needs.
Do NOT add Tokenomics/OnChain/Developer/Score models here yet; they belong
to their own phases (4, 5, 6, 2) so each migration maps to one reviewable
chunk instead of one giant initial migration nobody can review.
"""

import uuid

from django.db import models


class Blockchain(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(unique=True)  # e.g. "ethereum", "solana"
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class Asset(models.Model):
    """The canonical, deduplicated representation of a crypto asset.
    Identity resolution (section 11) lives on top of this model — a given
    real-world asset should map to exactly one Asset row even if it has
    multiple provider ids or contract addresses across chains."""

    class Sector(models.TextChoices):
        """Controlled taxonomy per section 21. Deliberately a fixed list,
        not free text — sector-aware scoring/comparables only works if
        assets are bucketed consistently. Unclassified assets stay NULL
        rather than being forced into a wrong bucket."""

        DEFI = "DeFi", "DeFi"
        L1 = "L1", "L1"
        L2 = "L2", "L2"
        RWA = "RWA", "RWA"
        DEPIN = "DePIN", "DePIN"
        AI = "AI", "AI"
        INFRASTRUCTURE = "Infrastructure", "Infrastructure"
        PAYMENTS = "Payments", "Payments"
        STABLECOIN_INFRA = "Stablecoin Infrastructure", "Stablecoin Infrastructure"
        GAMING = "Gaming", "Gaming"
        SOCIAL = "Social", "Social"
        PRIVACY = "Privacy", "Privacy"
        ORACLE = "Oracle", "Oracle"
        INTEROPERABILITY = "Interoperability", "Interoperability"
        DEX = "DEX", "DEX"
        LENDING = "Lending", "Lending"
        DERIVATIVES = "Derivatives", "Derivatives"
        MEME = "Meme", "Meme"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    symbol = models.CharField(max_length=32, db_index=True)
    name = models.CharField(max_length=200)

    # Provider-native identifiers, namespaced by provider, e.g.
    # {"coingecko": "bitcoin", "coinmarketcap": "1"}. Kept as JSON rather
    # than a separate AssetIdentifier table for now — promote to a real
    # table in Phase 3+ once we have more than one provider and need to
    # query "find asset by provider+id" efficiently.
    external_ids = models.JSONField(default=dict, blank=True)

    sector = models.CharField(max_length=30, choices=Sector.choices, null=True, blank=True, db_index=True)
    raw_categories = models.JSONField(default=list, blank=True)  # unmapped provider categories, for audit
    github_repo_url = models.URLField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["symbol"]), models.Index(fields=["sector"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.symbol.upper()})"


class ContractAddress(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="contract_addresses")
    blockchain = models.ForeignKey(Blockchain, on_delete=models.PROTECT)
    address = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["blockchain", "address"], name="unique_contract_per_chain")
        ]

    def __str__(self) -> str:
        return f"{self.address} on {self.blockchain.slug}"


class MarketSnapshot(models.Model):
    """A historical point-in-time market data observation. Never overwritten
    — this table is append-only so backtesting (Phase 10) always has an
    honest, unmodified historical record. See section 9 / section 58."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="market_snapshots")

    price_usd = models.DecimalField(max_digits=36, decimal_places=18)
    market_cap_usd = models.DecimalField(max_digits=36, decimal_places=2, null=True, blank=True)
    fully_diluted_valuation_usd = models.DecimalField(max_digits=36, decimal_places=2, null=True, blank=True)
    volume_24h_usd = models.DecimalField(max_digits=36, decimal_places=2, null=True, blank=True)
    circulating_supply = models.DecimalField(max_digits=36, decimal_places=8, null=True, blank=True)
    total_supply = models.DecimalField(max_digits=36, decimal_places=8, null=True, blank=True)
    max_supply = models.DecimalField(max_digits=36, decimal_places=8, null=True, blank=True)

    source = models.CharField(max_length=50)  # provider name, for auditability
    observed_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["asset", "observed_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "source", "observed_at"], name="unique_snapshot_per_source_time"
            )
        ]
        ordering = ["-observed_at"]

    def __str__(self) -> str:
        return f"{self.asset.symbol} @ {self.observed_at.isoformat()} (${self.price_usd})"


class DataIngestionJob(models.Model):
    """Tracks each ingestion run so failures/staleness are auditable
    (section 10 — data quality engine)."""

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        PARTIAL = "partial", "Partial success"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=50)
    job_type = models.CharField(max_length=50)  # e.g. "market_snapshot"
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    assets_attempted = models.PositiveIntegerField(default=0)
    assets_succeeded = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.provider}:{self.job_type} [{self.status}] {self.started_at}"


class DataQualityIssue(models.Model):
    """A flagged anomaly on a specific snapshot — missing/stale/impossible
    values, provider disagreement, etc. Kept separate from MarketSnapshot
    so raw data is never mutated, only annotated."""

    class Severity(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="data_quality_issues")
    ingestion_job = models.ForeignKey(DataIngestionJob, on_delete=models.SET_NULL, null=True, blank=True)
    issue_type = models.CharField(max_length=50)  # e.g. "price_anomaly", "stale_data"
    severity = models.CharField(max_length=20, choices=Severity.choices)
    details = models.JSONField(default=dict, blank=True)
    detected_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.issue_type} [{self.severity}] on {self.asset.symbol}"


class ScoreSnapshot(models.Model):
    """A point-in-time score for one asset from one model version. Never
    overwritten — append-only, same as MarketSnapshot, specifically so
    Phase 10 backtesting can answer "what did the scanner think about this
    asset N days ago" (section 9) without any risk of today's model
    silently rewriting yesterday's ranking."""

    class ModelName(models.TextChoices):
        TEN_X_POTENTIAL = "10x_potential", "10X Potential"
        UNDERVALUATION = "undervaluation", "Undervaluation"
        MOMENTUM = "momentum", "Momentum"
        RISK = "risk", "Risk"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="score_snapshots")
    market_snapshot = models.ForeignKey(
        MarketSnapshot, on_delete=models.PROTECT, related_name="score_snapshots"
    )

    model_name = models.CharField(max_length=30, choices=ModelName.choices)
    model_version = models.CharField(max_length=20)  # e.g. "v1.0"

    score = models.DecimalField(max_digits=6, decimal_places=2)
    data_confidence = models.DecimalField(max_digits=5, decimal_places=4)

    computed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["asset", "model_name", "computed_at"]),
            models.Index(fields=["model_name", "model_version", "computed_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "model_name", "model_version", "market_snapshot"],
                name="unique_score_per_asset_model_version_snapshot",
            )
        ]
        ordering = ["-computed_at"]

    def __str__(self) -> str:
        return f"{self.asset.symbol} {self.model_name} {self.model_version} = {self.score}"


class ScoreFactor(models.Model):
    """One named, weighted input to a ScoreSnapshot — the explainability
    record required by section 2 ('every score must answer: which metrics
    contributed, which hurt it'). Mirrors core.scoring.base.Factor."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    score_snapshot = models.ForeignKey(ScoreSnapshot, on_delete=models.CASCADE, related_name="score_factors")

    name = models.CharField(max_length=50)
    weight = models.DecimalField(max_digits=6, decimal_places=2)
    normalized_value = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    raw_value = models.CharField(max_length=200, null=True, blank=True)
    insufficient_data = models.BooleanField(default=False)
    note = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["score_snapshot", "name"], name="unique_factor_per_score_snapshot")
        ]

    def __str__(self) -> str:
        value = "insufficient_data" if self.insufficient_data else str(self.normalized_value)
        return f"{self.name}={value} (w={self.weight})"


class Protocol(models.Model):
    """A DeFi protocol tracked for fundamentals (TVL, fees, revenue).
    Linked to Asset where identity resolution succeeds (matched via
    DefiLlama's gecko_id against Asset.external_ids.coingecko) — not every
    protocol will have a matching Asset (e.g. multi-token protocols,
    protocols without a liquid governance token), so `asset` is nullable.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        Asset, on_delete=models.SET_NULL, null=True, blank=True, related_name="protocols"
    )
    slug = models.SlugField(unique=True)  # DefiLlama slug, e.g. "uniswap"
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True)
    chains = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name


class TVLSnapshot(models.Model):
    """Append-only TVL history for a Protocol, same pattern as
    MarketSnapshot — never overwritten, so backtesting has an honest
    historical record."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    protocol = models.ForeignKey(Protocol, on_delete=models.CASCADE, related_name="tvl_snapshots")

    tvl_usd = models.DecimalField(max_digits=36, decimal_places=2)
    change_1d_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    change_7d_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)

    source = models.CharField(max_length=50)
    observed_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["protocol", "observed_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["protocol", "source", "observed_at"], name="unique_tvl_snapshot_per_source_time"
            )
        ]
        ordering = ["-observed_at"]

    def __str__(self) -> str:
        return f"{self.protocol.slug} TVL @ {self.observed_at.isoformat()} (${self.tvl_usd})"


class FeeSnapshot(models.Model):
    """Append-only fee history for a Protocol. `fees_24h_usd` is the
    directly-observed figure; `fees_7d_usd`/`fees_30d_usd` are DefiLlama's
    own rolling totals, not derived here, so they reflect DefiLlama's
    methodology rather than ours."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    protocol = models.ForeignKey(Protocol, on_delete=models.CASCADE, related_name="fee_snapshots")

    fees_24h_usd = models.DecimalField(max_digits=36, decimal_places=2)
    fees_7d_usd = models.DecimalField(max_digits=36, decimal_places=2, null=True, blank=True)
    fees_30d_usd = models.DecimalField(max_digits=36, decimal_places=2, null=True, blank=True)

    source = models.CharField(max_length=50)
    observed_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["protocol", "observed_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["protocol", "source", "observed_at"], name="unique_fee_snapshot_per_source_time"
            )
        ]
        ordering = ["-observed_at"]

    def __str__(self) -> str:
        return f"{self.protocol.slug} fees @ {self.observed_at.isoformat()} (${self.fees_24h_usd}/24h)"


class RevenueSnapshot(models.Model):
    """Append-only protocol-revenue history — the share of fees the
    protocol itself captures (as opposed to fees paid out to LPs/stakers).
    A protocol with NO RevenueSnapshot doesn't mean missing data; it can
    mean the protocol takes no cut at all — that distinction matters for
    the Undervaluation Score and is preserved by simply not creating a
    row, rather than writing a fabricated 0."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    protocol = models.ForeignKey(Protocol, on_delete=models.CASCADE, related_name="revenue_snapshots")

    revenue_24h_usd = models.DecimalField(max_digits=36, decimal_places=2)

    source = models.CharField(max_length=50)
    observed_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["protocol", "observed_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["protocol", "source", "observed_at"], name="unique_revenue_snapshot_per_source_time"
            )
        ]
        ordering = ["-observed_at"]

    def __str__(self) -> str:
        return f"{self.protocol.slug} revenue @ {self.observed_at.isoformat()} (${self.revenue_24h_usd}/24h)"


class HolderSnapshot(models.Model):
    """Append-only holder-count/concentration history for a
    ContractAddress (not Asset directly — an asset can have multiple
    contracts across chains, and holder distribution genuinely differs
    per chain). `top_10_concentration_pct` is only populated where
    CoinGecko's holders data (Beta, coverage varies) reports it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contract_address = models.ForeignKey(
        ContractAddress, on_delete=models.CASCADE, related_name="holder_snapshots"
    )

    holder_count = models.PositiveIntegerField(null=True, blank=True)
    top_10_concentration_pct = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)

    source = models.CharField(max_length=50)
    observed_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["contract_address", "observed_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["contract_address", "source", "observed_at"],
                name="unique_holder_snapshot_per_source_time",
            )
        ]
        ordering = ["-observed_at"]

    def __str__(self) -> str:
        return f"{self.contract_address.address[:10]}... holders @ {self.observed_at.isoformat()} ({self.holder_count})"


class DeveloperActivitySnapshot(models.Model):
    """Append-only developer-activity history for an Asset's primary
    GitHub repo (Asset.github_repo_url). `commits_4w` is nullable — GitHub
    returns 202 (stats still computing) on some first requests, which is
    genuinely different from 0 commits and preserved as NULL, not 0."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="developer_activity_snapshots")

    stars = models.PositiveIntegerField()
    forks = models.PositiveIntegerField()
    open_issues = models.PositiveIntegerField()
    is_archived = models.BooleanField(default=False)
    repo_pushed_at = models.DateTimeField(null=True, blank=True)
    commits_4w = models.PositiveIntegerField(null=True, blank=True)

    source = models.CharField(max_length=50)
    observed_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["asset", "observed_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "source", "observed_at"], name="unique_dev_activity_per_source_time"
            )
        ]
        ordering = ["-observed_at"]

    def __str__(self) -> str:
        return f"{self.asset.symbol} dev activity @ {self.observed_at.isoformat()} ({self.commits_4w} commits/4w)"


class DEXPairSnapshot(models.Model):
    """Append-only DEX pair data from DEX Screener. Stores token-LEVEL
    aggregated data (summed liquidity/volume across all pairs for a token,
    averaged price, earliest pair creation time) — not individual pairs.
    Multiple DEX pairs per token are aggregated in the ingestion layer
    (core/tasks/dex_ingestion.py) before writing here.

    This model captures the on-chain trading activity that CoinGecko
    alone misses for new/small tokens: which DEXes they trade on, actual
    DEX liquidity depth, buy/sell pressure, and token age."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="dex_pair_snapshots")

    # Aggregated liquidity & volume across all DEX pairs for this token
    liquidity_usd = models.DecimalField(max_digits=36, decimal_places=2)
    volume_24h_usd = models.DecimalField(max_digits=36, decimal_places=2, null=True, blank=True)
    volume_6h_usd = models.DecimalField(max_digits=36, decimal_places=2, null=True, blank=True)
    volume_1h_usd = models.DecimalField(max_digits=36, decimal_places=2, null=True, blank=True)

    # Price changes (from the primary/highest-liquidity pair)
    price_change_24h_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    price_change_6h_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    price_change_1h_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)

    # Buy/sell activity (aggregated across all pairs)
    txns_24h_buys = models.PositiveIntegerField(null=True, blank=True)
    txns_24h_sells = models.PositiveIntegerField(null=True, blank=True)

    # Token age — earliest pair creation time across all DEX pairs
    earliest_pair_created_at = models.DateTimeField(null=True, blank=True)

    # How many DEX pairs exist for this token (proxy for multi-DEX presence)
    pair_count = models.PositiveIntegerField(default=1)

    # Chain(s) the token trades on
    chains = models.JSONField(default=list, blank=True)

    source = models.CharField(max_length=50)
    observed_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["asset", "observed_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "source", "observed_at"],
                name="unique_dex_snapshot_per_source_time",
            )
        ]
        ordering = ["-observed_at"]

    def __str__(self) -> str:
        return f"{self.asset.symbol} DEX @ {self.observed_at.isoformat()} (liq ${self.liquidity_usd:,.0f})"


class MarketRegimeSnapshot(models.Model):
    """Point-in-time market regime assessment derived from Binance public
    market data. One row per observation, append-only.

    The regime classification is a simplified trend assessment (not a
    trading signal) used to adjust scoring aggressiveness: in bullish
    conditions, the scanner can be more aggressive; in bearish conditions,
    it should be more conservative. This is context, not prediction."""

    class Regime(models.TextChoices):
        BULLISH = "bullish", "Bullish"
        BEARISH = "bearish", "Bearish"
        NEUTRAL = "neutral", "Neutral"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # BTC/ETH reference prices
    btc_price_usd = models.DecimalField(max_digits=36, decimal_places=2)
    eth_price_usd = models.DecimalField(max_digits=36, decimal_places=2)

    # 7-day price changes
    btc_change_7d_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    eth_change_7d_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)

    # BTC trend indicator — is price above its 50-day moving average?
    btc_above_50dma = models.BooleanField(null=True, blank=True)
    btc_50dma_value = models.DecimalField(max_digits=36, decimal_places=2, null=True, blank=True)

    # ETH/BTC ratio — altcoin strength proxy
    eth_btc_ratio = models.DecimalField(max_digits=20, decimal_places=10, null=True, blank=True)
    eth_btc_change_7d_pct = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)

    # BTC volume dominance approximation (volume-based, not market-cap-based)
    btc_volume_dominance_pct = models.DecimalField(max_digits=7, decimal_places=4, null=True, blank=True)

    # Total market context
    total_usdt_volume_24h_usd = models.DecimalField(max_digits=36, decimal_places=2, null=True, blank=True)

    # Regime classification
    regime = models.CharField(max_length=10, choices=Regime.choices)
    regime_confidence = models.DecimalField(max_digits=5, decimal_places=4)

    source = models.CharField(max_length=50)
    observed_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["observed_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "observed_at"],
                name="unique_regime_snapshot_per_source_time",
            )
        ]
        ordering = ["-observed_at"]

    def __str__(self) -> str:
        return f"Market Regime: {self.regime} @ {self.observed_at.isoformat()} (BTC ${self.btc_price_usd:,.0f})"


class Catalyst(models.Model):
    """A verifiable upcoming or past event for an asset (section 23).

    Per the spec: "Never create fake catalysts. Never infer a partnership
    from a rumor and present it as fact." No free, licensable automated
    events feed was found during Phase 8 research (CoinMarketCal's free
    tier is personal-use licensed only; see docs/DATA_LICENSING.md) — so
    every row here is manually curated (via the add_catalyst management
    command) from a real, cited source, not bulk-ingested. This is a
    deliberate design choice, not a placeholder for automation to replace
    later without re-examining the licensing question.

    Unlike the append-only *Snapshot models elsewhere in this schema,
    Catalyst rows ARE mutated in place (status changes as an event
    resolves) — a catalyst is a single evolving record of one real-world
    event, not a repeated point-in-time observation."""

    class CatalystType(models.TextChoices):
        MAINNET = "mainnet", "Mainnet"
        TOKEN_LAUNCH = "token_launch", "Token Launch"
        MAJOR_UPGRADE = "major_upgrade", "Major Upgrade"
        PROTOCOL_RELEASE = "protocol_release", "Protocol Release"
        EXCHANGE_LISTING = "exchange_listing", "Exchange Listing"
        PARTNERSHIP = "partnership", "Partnership"
        PRODUCT_LAUNCH = "product_launch", "Product Launch"
        TOKEN_UNLOCK = "token_unlock", "Token Unlock"
        GOVERNANCE_EVENT = "governance_event", "Governance Event"
        INSTITUTIONAL_INTEGRATION = "institutional_integration", "Institutional Integration"
        ECOSYSTEM_EXPANSION = "ecosystem_expansion", "Major Ecosystem Expansion"

    class Status(models.TextChoices):
        UPCOMING = "upcoming", "Upcoming"
        CONFIRMED = "confirmed", "Confirmed"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        DELAYED = "delayed", "Delayed"

    class Confidence(models.TextChoices):
        """Deliberately categorical, not a fabricated numeric precision —
        section 23 warns against presenting inferred/rumored information
        as fact; a confidence LEVEL is honest about what a human curator
        can actually judge from a source, a confidence SCORE like "73%"
        would imply false precision."""

        CONFIRMED = "confirmed", "Confirmed by official source"
        LIKELY = "likely", "Likely (strong secondary sourcing)"
        SPECULATIVE = "speculative", "Speculative (unconfirmed rumor, tracked for awareness only)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="catalysts")

    title = models.CharField(max_length=200)
    description = models.TextField()
    catalyst_type = models.CharField(max_length=30, choices=CatalystType.choices)
    event_date = models.DateField()
    source_url = models.URLField()
    confidence = models.CharField(max_length=20, choices=Confidence.choices)
    impact_estimate = models.CharField(
        max_length=10,
        choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")],
        help_text="Curator's qualitative estimate, not a backtested prediction — see Confidence's own docstring "
        "for why this stays categorical rather than a fabricated numeric score.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UPCOMING)

    added_by = models.CharField(max_length=100, blank=True, help_text="Who curated this entry, for audit.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["asset", "event_date"]), models.Index(fields=["status", "event_date"])]
        ordering = ["event_date"]

    def __str__(self) -> str:
        return f"{self.asset.symbol}: {self.title} ({self.event_date}, {self.status})"


class Subscription(models.Model):
    """Tracks user subscription status, updated by Paystack webhook."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        NONE = "none", "No Subscription"

    user_id = models.CharField(
        max_length=255, unique=True,
        help_text="External user ID from the main site's JWT.",
    )
    email = models.EmailField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.NONE,
    )
    paystack_reference = models.CharField(max_length=255, blank=True, default="")
    plan = models.CharField(
        max_length=50, default="monthly",
        help_text="Subscription plan name (e.g. monthly, yearly).",
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user_id"]),
            models.Index(fields=["status", "expires_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} — {self.status} ({self.plan})"

    @property
    def is_valid(self) -> bool:
        if self.status != self.Status.ACTIVE:
            return False
        if self.expires_at is None:
            return False
        from django.utils import timezone
        return timezone.now() < self.expires_at


class AlertRule(models.Model):
    """A user-configurable alert rule (Roadmap Tier 1, Feature 2).

    A rule watches ONE asset (or all tracked assets when `asset` is NULL)
    and fires an AlertEvent whenever the latest snapshot crosses a
    threshold. Rules are evaluated on a schedule by core.tasks.alerts and
    never block ingestion — a rule that can't be evaluated simply doesn't
    fire that cycle.

    Channels are independent; a rule can fan out to email and/or Telegram.
    Delivery is best-effort: a failed email/Telegram send is recorded on
    the AlertEvent but does not take the whole rule down.
    """

    class Metric(models.TextChoices):
        SCORE_10X = "score_10x", "10X Potential score"
        SCORE_UNDERVALUATION = "score_undervaluation", "Undervaluation score"
        SCORE_MOMENTUM = "score_momentum", "Momentum score"
        SCORE_RISK = "score_risk", "Risk score"
        MARKET_CAP_PCT_CHANGE_24H = "market_cap_pct_change_24h", "Market cap 24h % change"
        VOLUME_PCT_CHANGE_24H = "volume_pct_change_24h", "Volume 24h % change"

    class Operator(models.TextChoices):
        GT = "gt", "greater than"
        GTE = "gte", "greater than or equal"
        LT = "lt", "less than"
        LTE = "lte", "less than or equal"

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        TELEGRAM = "telegram", "Telegram"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.CharField(
        max_length=255, db_index=True,
        help_text="External user ID from the main site's JWT.",
    )
    email = models.EmailField(blank=True, default="")
    telegram_chat_id = models.CharField(max_length=64, blank=True, default="")

    name = models.CharField(max_length=120, help_text="Human label, e.g. 'SOL 10X score > 80'.")
    asset = models.ForeignKey(
        Asset, on_delete=models.CASCADE, null=True, blank=True,
        help_text="The asset to watch, or NULL to watch all tracked assets.",
    )

    metric = models.CharField(max_length=30, choices=Metric.choices)
    operator = models.CharField(max_length=5, choices=Operator.choices, default=Operator.GT)
    threshold = models.DecimalField(max_digits=12, decimal_places=4)
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.EMAIL)

    is_active = models.BooleanField(default=True)
    cooldown_minutes = models.PositiveIntegerField(
        default=60,
        help_text="Minimum minutes between two fires of this rule FOR THE SAME ASSET, to prevent alert spam.",
    )
    last_fired_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Most recent fire across any asset — informational only. Cooldown is enforced per (rule, asset) "
        "using the alert history, so a global rule firing for one asset doesn't silence it for others.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user_id", "is_active"]),
            models.Index(fields=["asset", "is_active"]),
        ]
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        scope = self.asset.symbol if self.asset else "ALL"
        return f"{scope}: {self.metric} {self.operator} {self.threshold} -> {self.channel}"

    @property
    def in_cooldown(self) -> bool:
        """Legacy per-rule cooldown check. Prefer in_cooldown_for(asset)."""
        if self.last_fired_at is None:
            return False
        from django.utils import timezone
        from datetime import timedelta
        return timezone.now() < self.last_fired_at + timedelta(minutes=self.cooldown_minutes)

    def in_cooldown_for(self, asset) -> bool:
        """Whether this rule is in cooldown for a SPECIFIC asset, based on the
        most recent AlertEvent for that asset. Keeps a global rule able to
        fire for multiple assets within one cycle without spamming any single
        one."""
        from django.utils import timezone
        from datetime import timedelta
        recent = self.events.filter(asset=asset).order_by("-fired_at").first()
        if recent is None:
            return False
        return timezone.now() < recent.fired_at + timedelta(minutes=self.cooldown_minutes)


class AlertEvent(models.Model):
    """Append-only log of every time an alert rule fired (Feature 2 —
    alert history). Records both the evaluation result and whether the
    delivery (email/Telegram) succeeded, so users can audit what was sent
    and a failed delivery is not silently lost."""

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Delivery failed"
        ERROR = "error", "Evaluation error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule = models.ForeignKey(AlertRule, on_delete=models.CASCADE, related_name="events")
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="alert_events")

    metric = models.CharField(max_length=30)
    operator = models.CharField(max_length=5)
    threshold = models.DecimalField(max_digits=12, decimal_places=4)
    observed_value = models.DecimalField(max_digits=30, decimal_places=6)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SENT)
    channels = models.JSONField(default=list, blank=True)  # e.g. ["email", "telegram"]
    error_detail = models.TextField(blank=True)

    fired_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["rule", "fired_at"])]
        ordering = ["-fired_at"]

    def __str__(self) -> str:
        return f"{self.rule} fired @ {self.fired_at.isoformat()} ({self.status})"


class WebhookSubscription(models.Model):
    """A user/B2B client's registered endpoint to receive pushed events
    (Roadmap Tier 3, Feature 7 — webhook delivery for score changes).

    A subscription points at a target URL and opts into one or more event
    types from WebhookEvent.Event. When an event occurs (e.g. an asset's
    reward tier changes after a scoring run), core/webhooks.py finds all
    active subscriptions that match — either for that specific asset or
    for ALL assets — and POSTs the payload, signed with an HMAC-SHA256
    digest of the body using the subscription's shared `secret`, so the
    receiving service can verify authenticity.

    Delivery is best-effort: a non-2xx or transport failure is logged and
    the last_delivery status recorded, but never raises out of the scoring
    run.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.CharField(
        max_length=255, db_index=True,
        help_text="External user ID from the main site's JWT.",
    )
    name = models.CharField(max_length=120, help_text="Human label, e.g. 'Prod analytics endpoint'.")
    target_url = models.URLField()

    # Shared secret used to HMAC-sign event payloads (X-Webhook-Signature header).
    secret = models.CharField(max_length=128, blank=True, default="")

    # NULL = ALL assets; otherwise only events for this asset.
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, null=True, blank=True)

    event_types = models.JSONField(
        default=list, blank=True,
        help_text="List of event types to receive, e.g. [\"score.changed\"]. Empty = all events.",
    )

    is_active = models.BooleanField(default=True)
    last_delivery_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=20, blank=True, default="")  # e.g. "ok" / "error"

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["user_id", "is_active"])]
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        scope = self.asset.symbol if self.asset else "ALL"
        return f"{self.name} ({self.target_url}, {scope})"

    def accepts_event(self, event_type: str) -> bool:
        return (not self.event_types) or event_type in self.event_types


class ApiUsage(models.Model):
    """Per-user daily API call count, for B2B per-1,000-call billing (Roadmap
    Tier 3, Feature 7).

    Every authenticated API request increments the current day's counter for
    that user_id via an application-level middleware (not a raw DB UPDATE per
    request — see core/middleware.py which batches using update_or_create so
    the counter is coarse but cheap). Counts are rolled daily by the `date`
    primary key, so a site can be billed on its monthly/weekly call volume
    without keeping an unbounded per-request log.
    """

    id = models.BigAutoField(primary_key=True)
    user_id = models.CharField(max_length=255, db_index=True)
    date = models.DateField()
    call_count = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user_id", "date"], name="unique_daily_usage_per_user")
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.date}: {self.call_count} calls"


class TelegramBinding(models.Model):
    """Links a Telegram chat to a user account so the bot can act on the
    user's behalf (Roadmap Tier 3, Feature 8).

    Identity is bound securely: the user runs /link in a Telegram chat, which
    generates a one-time `verify_token` (replies to the chat with it). They
    then POST it — along with the chat_id — to the JWT-authenticated
    /api/v1/telegram/verify/ endpoint. Because that endpoint authenticates the
    caller, only the true owner of a user_id can attach that account to a
    Telegram chat; the binding flips to verified and /alerts becomes available.

    `/score` and `/top` are public and never require a binding. Telegram
    alerts created through the bot are stored as AlertRule rows with
    telegram_chat_id set to this chat.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat_id = models.CharField(max_length=64, unique=True, db_index=True)
    user_id = models.CharField(max_length=255, db_index=True)
    telegram_username = models.CharField(max_length=128, blank=True, default="")

    verify_token = models.CharField(max_length=64, blank=True, default="")
    is_verified = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["user_id", "is_verified"])]
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"chat {self.chat_id} -> user {self.user_id} ({'verified' if self.is_verified else 'unverified'})"
