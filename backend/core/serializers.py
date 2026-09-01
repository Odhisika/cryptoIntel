from rest_framework import serializers

from .models import (
    Asset, MarketSnapshot, ScoreSnapshot, ScoreFactor,
    Protocol, TVLSnapshot, FeeSnapshot, RevenueSnapshot,
    HolderSnapshot, DeveloperActivitySnapshot, Catalyst,
    DataQualityIssue, DataIngestionJob, AlertRule, AlertEvent,
    WebhookSubscription, ApiUsage,
)
from .scoring.tiers import classify_tier, TIER_LABELS, TIER_DESCRIPTIONS


class AssetListSerializer(serializers.ModelSerializer):
    latest_price = serializers.SerializerMethodField()
    latest_market_cap = serializers.SerializerMethodField()
    latest_volume = serializers.SerializerMethodField()
    sector_display = serializers.CharField(source="get_sector_display", read_only=True)
    score_10x = serializers.SerializerMethodField()
    score_undervaluation = serializers.SerializerMethodField()
    score_momentum = serializers.SerializerMethodField()
    score_risk = serializers.SerializerMethodField()
    tier = serializers.SerializerMethodField()

    class Meta:
        model = Asset
        fields = [
            "id", "symbol", "name", "sector", "sector_display",
            "is_active", "created_at",
            "latest_price", "latest_market_cap", "latest_volume",
            "score_10x", "score_undervaluation", "score_momentum", "score_risk",
            "tier",
        ]

    def _latest_snapshot(self, obj):
        snap = obj.market_snapshots.first()
        return snap

    def _latest_score(self, obj, model_name):
        score = obj.score_snapshots.filter(model_name=model_name).first()
        return score

    def get_latest_price(self, obj):
        snap = self._latest_snapshot(obj)
        return str(snap.price_usd) if snap else None

    def get_latest_market_cap(self, obj):
        snap = self._latest_snapshot(obj)
        return str(snap.market_cap_usd) if snap and snap.market_cap_usd else None

    def get_latest_volume(self, obj):
        snap = self._latest_snapshot(obj)
        return str(snap.volume_24h_usd) if snap and snap.volume_24h_usd else None

    def get_score_10x(self, obj):
        score = self._latest_score(obj, "10x_potential")
        if not score:
            return None
        return {"score": str(score.score), "confidence": str(score.data_confidence), "version": score.model_version}

    def get_score_undervaluation(self, obj):
        score = self._latest_score(obj, "undervaluation")
        if not score:
            return None
        return {"score": str(score.score), "confidence": str(score.data_confidence), "version": score.model_version}

    def get_score_momentum(self, obj):
        score = self._latest_score(obj, "momentum")
        if not score:
            return None
        return {"score": str(score.score), "confidence": str(score.data_confidence), "version": score.model_version}

    def get_score_risk(self, obj):
        score = self._latest_score(obj, "risk")
        if not score:
            return None
        return {"score": str(score.score), "confidence": str(score.data_confidence), "version": score.model_version}

    def get_tier(self, obj):
        """Compute the risk/reward tier from the 4 scores."""
        scores = {}
        for model_name in ["10x_potential", "undervaluation", "momentum", "risk"]:
            snap = obj.score_snapshots.filter(model_name=model_name).order_by("-computed_at").first()
            if snap:
                scores[model_name] = snap

        if not scores:
            return {"tier": "unclassified", "label": "Unclassified", "description": "Not enough data to classify."}

        tier_result = classify_tier(
            score_10x=scores.get("10x_potential").score if "10x_potential" in scores else None,
            score_risk=scores.get("risk").score if "risk" in scores else None,
            score_momentum=scores.get("momentum").score if "momentum" in scores else None,
            score_undervaluation=scores.get("undervaluation").score if "undervaluation" in scores else None,
            data_confidence_10x=scores.get("10x_potential").data_confidence if "10x_potential" in scores else None,
            data_confidence_risk=scores.get("risk").data_confidence if "risk" in scores else None,
            data_confidence_momentum=scores.get("momentum").data_confidence if "momentum" in scores else None,
            data_confidence_undervaluation=scores.get("undervaluation").data_confidence if "undervaluation" in scores else None,
        )
        return {
            "tier": tier_result.tier.value,
            "label": tier_result.label,
            "description": tier_result.description,
            "confidence": str(tier_result.confidence),
            "reasoning": tier_result.reasoning,
        }


class ScoreFactorSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScoreFactor
        fields = ["name", "weight", "normalized_value", "raw_value", "insufficient_data", "note"]


class ScoreSnapshotSerializer(serializers.ModelSerializer):
    factors = ScoreFactorSerializer(many=True, read_only=True)
    asset_symbol = serializers.CharField(source="asset.symbol", read_only=True)
    asset_name = serializers.CharField(source="asset.name", read_only=True)

    class Meta:
        model = ScoreSnapshot
        fields = [
            "id", "asset", "asset_symbol", "asset_name",
            "model_name", "model_version", "score", "data_confidence",
            "computed_at", "factors",
        ]


class AssetDetailSerializer(serializers.ModelSerializer):
    latest_market = serializers.SerializerMethodField()
    scores = serializers.SerializerMethodField()
    protocols = serializers.SerializerMethodField()
    catalysts = serializers.SerializerMethodField()
    developer_activity = serializers.SerializerMethodField()
    holder_data = serializers.SerializerMethodField()
    sector_display = serializers.CharField(source="get_sector_display", read_only=True)

    class Meta:
        model = Asset
        fields = [
            "id", "symbol", "name", "sector", "sector_display",
            "external_ids", "github_repo_url", "is_active",
            "created_at", "updated_at",
            "latest_market", "scores", "protocols", "catalysts",
            "developer_activity", "holder_data",
        ]

    def get_latest_market(self, obj):
        snap = obj.market_snapshots.first()
        if not snap:
            return None
        return {
            "price_usd": str(snap.price_usd),
            "market_cap_usd": str(snap.market_cap_usd) if snap.market_cap_usd else None,
            "fdv_usd": str(snap.fully_diluted_valuation_usd) if snap.fully_diluted_valuation_usd else None,
            "volume_24h_usd": str(snap.volume_24h_usd) if snap.volume_24h_usd else None,
            "circulating_supply": str(snap.circulating_supply) if snap.circulating_supply else None,
            "total_supply": str(snap.total_supply) if snap.total_supply else None,
            "max_supply": str(snap.max_supply) if snap.max_supply else None,
            "observed_at": snap.observed_at.isoformat(),
        }

    def get_scores(self, obj):
        scores = obj.score_snapshots.all()[:8]
        result = {}
        for s in scores:
            if s.model_name not in result:
                result[s.model_name] = {
                    "score": str(s.score),
                    "confidence": str(s.data_confidence),
                    "version": s.model_version,
                    "computed_at": s.computed_at.isoformat(),
                    "factors": ScoreFactorSerializer(s.score_factors.all(), many=True).data,
                }
        return result

    def get_protocols(self, obj):
        protos = obj.protocols.all()
        result = []
        for p in protos:
            tvl = p.tvl_snapshots.first()
            fee = p.fee_snapshots.first()
            rev = p.revenue_snapshots.first()
            result.append({
                "slug": p.slug,
                "name": p.name,
                "category": p.category,
                "chains": p.chains,
                "tvl_usd": str(tvl.tvl_usd) if tvl else None,
                "fees_24h": str(fee.fees_24h_usd) if fee else None,
                "revenue_24h": str(rev.revenue_24h_usd) if rev else None,
            })
        return result

    def get_catalysts(self, obj):
        cats = obj.catalysts.all()[:10]
        return [{
            "id": str(c.id),
            "title": c.title,
            "description": c.description,
            "catalyst_type": c.catalyst_type,
            "event_date": c.event_date.isoformat(),
            "confidence": c.confidence,
            "impact_estimate": c.impact_estimate,
            "status": c.status,
        } for c in cats]

    def get_developer_activity(self, obj):
        dev = obj.developer_activity_snapshots.first()
        if not dev:
            return None
        return {
            "stars": dev.stars,
            "forks": dev.forks,
            "open_issues": dev.open_issues,
            "is_archived": dev.is_archived,
            "commits_4w": dev.commits_4w,
            "observed_at": dev.observed_at.isoformat(),
        }

    def get_holder_data(self, obj):
        holders = HolderSnapshot.objects.filter(
            contract_address__asset=obj
        ).select_related("contract_address__blockchain")[:5]
        return [{
            "blockchain": h.contract_address.blockchain.slug,
            "address": h.contract_address.address,
            "holder_count": h.holder_count,
            "top_10_concentration_pct": str(h.top_10_concentration_pct) if h.top_10_concentration_pct else None,
        } for h in holders]


class MarketSnapshotSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source="asset.symbol", read_only=True)
    asset_name = serializers.CharField(source="asset.name", read_only=True)

    class Meta:
        model = MarketSnapshot
        fields = [
            "id", "asset", "asset_symbol", "asset_name",
            "price_usd", "market_cap_usd", "fully_diluted_valuation_usd",
            "volume_24h_usd", "circulating_supply", "total_supply", "max_supply",
            "source", "observed_at",
        ]


class ProtocolSerializer(serializers.ModelSerializer):
    latest_tvl = serializers.SerializerMethodField()
    latest_fees = serializers.SerializerMethodField()
    latest_revenue = serializers.SerializerMethodField()
    asset_symbol = serializers.SerializerMethodField()

    class Meta:
        model = Protocol
        fields = [
            "id", "slug", "name", "category", "chains", "is_active",
            "latest_tvl", "latest_fees", "latest_revenue", "asset_symbol",
        ]

    def get_latest_tvl(self, obj) -> str | None:
        tvl = obj.tvl_snapshots.first()
        return str(tvl.tvl_usd) if tvl else None

    def get_latest_fees(self, obj) -> str | None:
        fee = obj.fee_snapshots.first()
        return str(fee.fees_24h_usd) if fee else None

    def get_latest_revenue(self, obj) -> str | None:
        rev = obj.revenue_snapshots.first()
        return str(rev.revenue_24h_usd) if rev else None

    def get_asset_symbol(self, obj) -> str | None:
        return obj.asset.symbol if obj.asset else None


class CatalystSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source="asset.symbol", read_only=True)

    class Meta:
        model = Catalyst
        fields = [
            "id", "asset", "asset_symbol", "title", "description",
            "catalyst_type", "event_date", "source_url",
            "confidence", "impact_estimate", "status", "added_by",
        ]


class DataQualityIssueSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source="asset.symbol", read_only=True)

    class Meta:
        model = DataQualityIssue
        fields = [
            "id", "asset", "asset_symbol", "issue_type", "severity",
            "details", "detected_at",
        ]


class IngestionJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataIngestionJob
        fields = [
            "id", "provider", "job_type", "status",
            "assets_attempted", "assets_succeeded", "error_summary",
            "started_at", "finished_at",
        ]


class AlertRuleSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source="asset.symbol", read_only=True)
    metric_display = serializers.CharField(source="get_metric_display", read_only=True)
    operator_display = serializers.CharField(source="get_operator_display", read_only=True)
    channel_display = serializers.CharField(source="get_channel_display", read_only=True)

    class Meta:
        model = AlertRule
        fields = [
            "id", "name", "asset", "asset_symbol",
            "metric", "metric_display",
            "operator", "operator_display",
            "threshold", "channel", "channel_display",
            "email", "telegram_chat_id",
            "is_active", "cooldown_minutes", "last_fired_at",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "last_fired_at", "created_at", "updated_at"]

    def validate(self, attrs):
        channel = attrs.get("channel", getattr(self.instance, "channel", None))
        email = attrs.get("email", getattr(self.instance, "email", ""))
        telegram = attrs.get("telegram_chat_id", getattr(self.instance, "telegram_chat_id", ""))
        if channel == AlertRule.Channel.EMAIL and not email:
            raise serializers.ValidationError("Email channel requires an 'email' address.")
        if channel == AlertRule.Channel.TELEGRAM and not telegram:
            raise serializers.ValidationError("Telegram channel requires a 'telegram_chat_id'.")
        return attrs


class AlertEventSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source="asset.symbol", read_only=True)
    rule_name = serializers.CharField(source="rule.name", read_only=True)
    metric_display = serializers.SerializerMethodField()

    class Meta:
        model = AlertEvent
        fields = [
            "id", "rule", "rule_name", "asset", "asset_symbol",
            "metric", "metric_display", "operator", "threshold",
            "observed_value", "status", "channels", "error_detail", "fired_at",
        ]

    def get_metric_display(self, obj):
        return AlertRule.Metric(obj.metric).label if obj.metric in AlertRule.Metric.values else obj.metric


class WebhookSubscriptionSerializer(serializers.ModelSerializer):
    asset_symbol = serializers.CharField(source="asset.symbol", read_only=True)

    class Meta:
        model = WebhookSubscription
        fields = [
            "id", "name", "target_url", "secret",
            "asset", "asset_symbol", "event_types",
            "is_active", "last_delivery_at", "last_status",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "last_delivery_at", "last_status", "created_at", "updated_at"]
        extra_kwargs = {
            "secret": {"write_only": True, "help_text": "Shared secret used to HMAC-sign event payloads."},
        }

    def validate(self, attrs):
        event_types = attrs.get("event_types", getattr(self.instance, "event_types", []))
        from core.webhooks import Event
        valid = {Event.SCORE_CHANGED}
        unknown = set(event_types) - valid
        if unknown:
            raise serializers.ValidationError(f"Unknown event types: {sorted(unknown)}")
        return attrs


class ApiUsageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiUsage
        fields = ["date", "call_count", "updated_at"]
