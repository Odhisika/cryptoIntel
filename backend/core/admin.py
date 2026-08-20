from django.contrib import admin

from core.models import (
    Asset,
    Blockchain,
    Catalyst,
    ContractAddress,
    DataIngestionJob,
    DataQualityIssue,
    DeveloperActivitySnapshot,
    FeeSnapshot,
    HolderSnapshot,
    MarketSnapshot,
    Protocol,
    RevenueSnapshot,
    ScoreFactor,
    ScoreSnapshot,
    TVLSnapshot,
)


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("symbol", "name", "sector", "github_repo_url", "is_active", "updated_at")
    search_fields = ("symbol", "name")
    list_filter = ("is_active", "sector")


@admin.register(Blockchain)
class BlockchainAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")


@admin.register(ContractAddress)
class ContractAddressAdmin(admin.ModelAdmin):
    list_display = ("asset", "blockchain", "address")
    search_fields = ("address",)


class MarketSnapshotAdmin(admin.ModelAdmin):
    list_display = ("asset", "price_usd", "market_cap_usd", "source", "observed_at")
    list_filter = ("source",)
    search_fields = ("asset__symbol",)
    date_hierarchy = "observed_at"


admin.site.register(MarketSnapshot, MarketSnapshotAdmin)


@admin.register(DataIngestionJob)
class DataIngestionJobAdmin(admin.ModelAdmin):
    list_display = ("provider", "job_type", "status", "assets_succeeded", "assets_attempted", "started_at")
    list_filter = ("status", "provider")


@admin.register(DataQualityIssue)
class DataQualityIssueAdmin(admin.ModelAdmin):
    list_display = ("asset", "issue_type", "severity", "detected_at", "resolved_at")
    list_filter = ("severity", "issue_type")


@admin.register(Protocol)
class ProtocolAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "asset", "category", "is_active")
    search_fields = ("name", "slug")
    list_filter = ("category", "is_active")


@admin.register(TVLSnapshot)
class TVLSnapshotAdmin(admin.ModelAdmin):
    list_display = ("protocol", "tvl_usd", "change_1d_pct", "change_7d_pct", "observed_at")
    search_fields = ("protocol__slug",)
    date_hierarchy = "observed_at"


@admin.register(FeeSnapshot)
class FeeSnapshotAdmin(admin.ModelAdmin):
    list_display = ("protocol", "fees_24h_usd", "fees_7d_usd", "fees_30d_usd", "observed_at")
    search_fields = ("protocol__slug",)
    date_hierarchy = "observed_at"


@admin.register(RevenueSnapshot)
class RevenueSnapshotAdmin(admin.ModelAdmin):
    list_display = ("protocol", "revenue_24h_usd", "observed_at")
    search_fields = ("protocol__slug",)
    date_hierarchy = "observed_at"


@admin.register(HolderSnapshot)
class HolderSnapshotAdmin(admin.ModelAdmin):
    list_display = ("contract_address", "holder_count", "top_10_concentration_pct", "observed_at")
    search_fields = ("contract_address__address", "contract_address__asset__symbol")
    date_hierarchy = "observed_at"


@admin.register(DeveloperActivitySnapshot)
class DeveloperActivitySnapshotAdmin(admin.ModelAdmin):
    list_display = ("asset", "stars", "commits_4w", "is_archived", "observed_at")
    search_fields = ("asset__symbol",)
    list_filter = ("is_archived",)
    date_hierarchy = "observed_at"


@admin.register(Catalyst)
class CatalystAdmin(admin.ModelAdmin):
    list_display = ("asset", "title", "catalyst_type", "event_date", "confidence", "impact_estimate", "status")
    search_fields = ("asset__symbol", "title")
    list_filter = ("status", "catalyst_type", "confidence", "impact_estimate")
    date_hierarchy = "event_date"


class ScoreFactorInline(admin.TabularInline):
    model = ScoreFactor
    extra = 0
    readonly_fields = ("name", "weight", "normalized_value", "raw_value", "insufficient_data", "note")
    can_delete = False


@admin.register(ScoreSnapshot)
class ScoreSnapshotAdmin(admin.ModelAdmin):
    list_display = ("asset", "model_name", "model_version", "score", "data_confidence", "computed_at")
    list_filter = ("model_name", "model_version")
    search_fields = ("asset__symbol",)
    inlines = [ScoreFactorInline]
