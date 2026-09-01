from decimal import Decimal

from django.db.models import Q, Count, F, OuterRef, Subquery, Max, Value
from django.db.models.functions import Coalesce
from django.shortcuts import render, get_object_or_404
from rest_framework import generics
from rest_framework.decorators import api_view
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer, OpenApiParameter, OpenApiRequest
from rest_framework import serializers as drf_serializers
from rest_framework.response import Response
from rest_framework.reverse import reverse

from .models import (
    Asset, MarketSnapshot, ScoreSnapshot, ScoreFactor,
    Protocol, TVLSnapshot, Catalyst, DataIngestionJob, DataQualityIssue,
    AlertRule, AlertEvent, WebhookSubscription, ApiUsage,
)
from .serializers import (
    AssetListSerializer, AssetDetailSerializer,
    ScoreSnapshotSerializer, ProtocolSerializer,
    CatalystSerializer, IngestionJobSerializer,
    DataQualityIssueSerializer, AlertRuleSerializer, AlertEventSerializer,
    WebhookSubscriptionSerializer, ApiUsageSerializer,
)
from .scoring.tiers import classify_tier, RewardTier, TIER_LABELS
from core.backtest import build_backtest_report


class AssetListView(generics.ListAPIView):
    serializer_class = AssetListSerializer

    def get_queryset(self):
        qs = Asset.objects.prefetch_related(
            "market_snapshots", "score_snapshots",
        ).all()

        # search
        q = self.request.query_params.get("search")
        if q:
            qs = qs.filter(
                Q(name__icontains=q) | Q(symbol__icontains=q)
            )

        # sector filter
        sector = self.request.query_params.get("sector")
        if sector:
            qs = qs.filter(sector=sector)

        # tier filter — filter assets by their computed risk/reward tier
        tier = self.request.query_params.get("tier")
        if tier:
            valid_tiers = [t.value for t in RewardTier] + ["unclassified"]
            if tier in valid_tiers:
                # We can't filter by tier in the DB directly since it's
                # computed from scores. Instead, we filter in-memory after
                # fetching. For large datasets, consider caching tiers.
                qs = self._filter_by_tier(qs, tier)

        # active filter
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("true", "1"))

        # sort
        sort = self.request.query_params.get("sort", "-created_at")
        allowed_sorts = {
            "symbol", "-symbol",
            "name", "-name",
            "sector", "-sector",
            "created_at", "-created_at",
            "updated_at", "-updated_at",
        }
        if sort in allowed_sorts:
            qs = qs.order_by(sort)

        return qs

    def _filter_by_tier(self, qs, tier_value):
        """Filter queryset by computed tier. Computed in Python since tier
        depends on 4 scores that can't be expressed in a single DB query."""
        asset_ids = []
        for asset in qs:
            scores = {}
            for model_name in ["10x_potential", "undervaluation", "momentum", "risk"]:
                snap = asset.score_snapshots.filter(model_name=model_name).order_by("-computed_at").first()
                if snap:
                    scores[model_name] = snap

            if not scores:
                if tier_value == "unclassified":
                    asset_ids.append(asset.id)
                continue

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
            if tier_result.tier.value == tier_value:
                asset_ids.append(asset.id)

        return qs.filter(id__in=asset_ids)


class AssetDetailView(generics.RetrieveAPIView):
    serializer_class = AssetDetailSerializer
    queryset = Asset.objects.prefetch_related(
        "market_snapshots", "score_snapshots", "score_snapshots__score_factors",
        "protocols", "catalysts", "developer_activity_snapshots",
        "contract_addresses__holder_snapshots",
    ).all()
    lookup_field = "id"


class ScoreRankingView(generics.ListAPIView):
    serializer_class = ScoreSnapshotSerializer

    def get_queryset(self):
        model_name = self.request.query_params.get("model_name", "10x_potential")
        min_confidence = self.request.query_params.get("min_confidence")

        qs = (
            ScoreSnapshot.objects
            .filter(model_name=model_name)
            .select_related("asset")
            .prefetch_related("score_factors")
        )

        if min_confidence is not None:
            qs = qs.filter(data_confidence__gte=min_confidence)

        # latest score per asset: subquery for max computed_at
        latest = (
            ScoreSnapshot.objects
            .filter(
                asset=OuterRef("asset"),
                model_name=model_name,
            )
            .order_by("-computed_at")
            .values("id")[:1]
        )
        qs = qs.filter(id__in=Subquery(latest)).order_by("-score")

        return qs


class ProtocolListView(generics.ListAPIView):
    serializer_class = ProtocolSerializer
    queryset = Protocol.objects.select_related("asset").prefetch_related(
        "tvl_snapshots", "fee_snapshots", "revenue_snapshots",
    ).all()

    def get_queryset(self):
        qs = super().get_queryset()

        category = self.request.query_params.get("category")
        if category:
            qs = qs.filter(category__icontains=category)

        q = self.request.query_params.get("search")
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(slug__icontains=q))

        return qs


class CatalystListView(generics.ListAPIView):
    serializer_class = CatalystSerializer

    def get_queryset(self):
        qs = Catalyst.objects.select_related("asset").all()

        status = self.request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)

        asset = self.request.query_params.get("asset")
        if asset:
            qs = qs.filter(asset__id=asset)

        catalyst_type = self.request.query_params.get("catalyst_type")
        if catalyst_type:
            qs = qs.filter(catalyst_type=catalyst_type)

        return qs


class AlertRuleListCreateView(generics.ListCreateAPIView):
    """Create and list alert rules for the authenticated user."""

    serializer_class = AlertRuleSerializer

    def get_queryset(self):
        user_id = self.request.auth
        return AlertRule.objects.select_related("asset").filter(user_id=user_id).order_by("-updated_at")

    def perform_create(self, serializer):
        serializer.save(user_id=self.request.auth)


class AlertRuleDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update, or delete one of the authenticated user's alert rules."""

    serializer_class = AlertRuleSerializer
    lookup_field = "id"

    def get_queryset(self):
        user_id = self.request.auth
        return AlertRule.objects.select_related("asset").filter(user_id=user_id)


class AlertEventListView(generics.ListAPIView):
    """Alert history log for the authenticated user (Feature 2)."""

    serializer_class = AlertEventSerializer

    def get_queryset(self):
        user_id = self.request.auth
        qs = (
            AlertEvent.objects
            .filter(rule__user_id=user_id)
            .select_related("rule", "asset")
            .order_by("-fired_at")
        )
        rule_id = self.request.query_params.get("rule")
        if rule_id:
            qs = qs.filter(rule_id=rule_id)
        status = self.request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)
        return qs


@extend_schema(
    tags=["Webhooks"],
    request=WebhookSubscriptionSerializer,
    responses={200: WebhookSubscriptionSerializer(many=True), 201: WebhookSubscriptionSerializer()},
)
class WebhookSubscriptionListCreateView(generics.ListCreateAPIView):
    """List and create webhook subscriptions for the authenticated user (B2B,
    Feature 7). Each subscription targets a URL that will receive pushed
    score.changed events, signed with its shared secret."""

    serializer_class = WebhookSubscriptionSerializer

    def get_queryset(self):
        user_id = self.request.auth
        return (
            WebhookSubscription.objects
            .select_related("asset")
            .filter(user_id=user_id)
            .order_by("-updated_at")
        )

    def perform_create(self, serializer):
        serializer.save(user_id=self.request.auth)


@extend_schema(
    tags=["Webhooks"],
    request=WebhookSubscriptionSerializer,
    responses={200: WebhookSubscriptionSerializer()},
)
class WebhookSubscriptionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, update, or delete one of the authenticated user's webhook
    subscriptions (Feature 7)."""

    serializer_class = WebhookSubscriptionSerializer
    lookup_field = "id"

    def get_queryset(self):
        return WebhookSubscription.objects.filter(user_id=self.request.auth)


@extend_schema(
    tags=["Usage"],
    responses={200: drf_serializers.DictField()},
)
class ApiUsageView(generics.ListAPIView):
    """API call usage for the authenticated user, for B2B per-1,000-call
    billing (Feature 7). Lists daily call counts, most recent first."""

    serializer_class = ApiUsageSerializer
    pagination_class = None

    def get_queryset(self):
        return ApiUsage.objects.filter(user_id=self.request.auth).order_by("-date")


@extend_schema(
    tags=["Telegram"],
    request=OpenApiRequest(
        drf_serializers.DictField(),
        examples=None,
    ),
    responses={200: drf_serializers.DictField()},
)
@api_view(["POST"])
def telegram_bind_view(request):
    """Verify a Telegram binding (Feature 8). Authenticated: the caller proves
    they own the user_id attached to the JWT, so only the real account owner
    can bind their chat to /alerts."""
    from core.models import TelegramBinding

    try:
        chat_id = str(request.data.get("chat_id", "")).strip()
        verify_token = str(request.data.get("verify_token", "")).strip()
    except AttributeError:
        return Response({"error": "chat_id and verify_token required"}, status=400)

    if not chat_id or not verify_token:
        return Response({"error": "chat_id and verify_token required"}, status=400)

    try:
        binding = TelegramBinding.objects.get(chat_id=chat_id)
    except TelegramBinding.DoesNotExist:
        return Response({"error": "No pending link for this chat_id. Run /link first."}, status=404)

    if not binding.verify_token or binding.verify_token != verify_token:
        return Response({"error": "Invalid verify_token."}, status=400)

    binding.user_id = self_user_id = request.auth
    binding.is_verified = True
    binding.verify_token = ""
    binding.save(update_fields=["user_id", "is_verified", "verify_token", "updated_at"])

    return Response({"ok": True, "chat_id": chat_id, "user_id": self_user_id})


@extend_schema(
    responses={200: drf_serializers.DictField()},
)
@api_view(["GET"])
def backtest_accuracy_view(request):
    """Historical score accuracy / backtest stats (Feature 3).

    Returns forward-return performance (win rate, avg return, annualized
    Sharpe) per reward tier and horizon, plus a saleable headline. Optionally
    narrow to a model version with ?model_version=v1.0.
    """
    from decimal import Decimal

    model_version = request.query_params.get("model_version") or None
    report = build_backtest_report(model_version=model_version)

    body = _decimal_to_str(report)
    body["model_version"] = model_version
    return Response(body)


def _decimal_to_str(obj):
    """Recursively convert Decimal values in the report to strings so the
    response is clean JSON (DRF's Response canonicalizes Decimals to floats,
    which we want to avoid for money-ish numbers)."""
    if isinstance(obj, dict):
        return {k: _decimal_to_str(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_to_str(v) for v in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    return obj


@extend_schema(
    responses=OpenApiResponse(
        response=inline_serializer(
            name="DashboardStats",
            fields={
                "total_assets": drf_serializers.IntegerField(),
                "total_scores": drf_serializers.IntegerField(),
                "sector_breakdown": drf_serializers.ListField(child=drf_serializers.DictField()),
                "latest_ingestion_job": drf_serializers.DictField(allow_null=True),
            },
        )
    ),
)
@api_view(["GET"])
def dashboard_stats_view(request):
    total_assets = Asset.objects.filter(is_active=True).count()
    total_scores = ScoreSnapshot.objects.count()

    sector_breakdown = (
        Asset.objects
        .filter(is_active=True)
        .values("sector")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    latest_job = DataIngestionJob.objects.order_by("-started_at").first()
    latest_job_data = (
        IngestionJobSerializer(latest_job).data if latest_job else None
    )

    return Response({
        "total_assets": total_assets,
        "total_scores": total_scores,
        "sector_breakdown": list(sector_breakdown),
        "latest_ingestion_job": latest_job_data,
    })


@extend_schema(
    responses=OpenApiResponse(
        response=inline_serializer(
            name="TierSummary",
            fields={
                "tiers": drf_serializers.ListField(
                    child=inline_serializer(
                        name="TierSummaryEntry",
                        fields={
                            "tier": drf_serializers.CharField(),
                            "label": drf_serializers.CharField(),
                            "count": drf_serializers.IntegerField(),
                            "description": drf_serializers.CharField(),
                        },
                    )
                ),
                "total": drf_serializers.IntegerField(),
            },
        )
    ),
)
@api_view(["GET"])
def tier_summary_view(request):
    """Return a summary of how many assets fall into each tier."""
    from collections import Counter

    tier_counts = Counter()

    latest_per_asset = (
        ScoreSnapshot.objects
        .filter(model_name="10x_potential")
        .values("asset_id")
        .annotate(latest=Max("computed_at"))
    )

    for row in latest_per_asset:
        asset = Asset.objects.filter(id=row["asset_id"]).first()
        if not asset:
            continue

        scores = {}
        for model_name in ["10x_potential", "undervaluation", "momentum", "risk"]:
            snap = asset.score_snapshots.filter(model_name=model_name).order_by("-computed_at").first()
            if snap:
                scores[model_name] = snap

        if not scores:
            tier_counts["unclassified"] += 1
            continue

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
        tier_counts[tier_result.tier.value] += 1

    tier_descriptions = {
        "2x_safe": "Lower risk, established projects with steady fundamentals.",
        "3x_growth": "Balanced risk/reward. Solid projects with room to grow.",
        "10x_potential": "Higher risk, high reward. Strong signals across metrics.",
        "moonshot": "Speculative, very high risk. Could 50x or go to zero.",
    }

    tiers = []
    for t in RewardTier:
        tiers.append({
            "tier": t.value,
            "label": TIER_LABELS[t],
            "count": tier_counts.get(t.value, 0),
            "description": tier_descriptions[t.value],
        })
    tiers.append({
        "tier": "unclassified",
        "label": "Unclassified",
        "count": tier_counts.get("unclassified", 0),
        "description": "Not enough data to classify.",
    })

    return Response({"tiers": tiers, "total": sum(tier_counts.values())})


@extend_schema(
    parameters=[OpenApiParameter(name="q", required=False, description="Token symbol or name search")],
    responses={200: drf_serializers.ListField(child=drf_serializers.DictField())},
)
@api_view(["GET"])
def asset_search_view(request):
    q = request.query_params.get("q", "").strip()
    if len(q) < 2:
        return Response([])

    assets = (
        Asset.objects
        .filter(Q(name__icontains=q) | Q(symbol__icontains=q))
        .filter(is_active=True)
        .values("id", "symbol", "name", "sector")[:10]
    )
    return Response(list(assets))


def api_root(request, format=None):
    return Response({
        "assets": reverse("asset-list", request=request, format=format),
        "tiers": reverse("tier-summary", request=request, format=format),
        "protocols": reverse("protocol-list", request=request, format=format),
        "catalysts": reverse("catalyst-list", request=request, format=format),
        "dashboard": reverse("dashboard-stats", request=request, format=format),
        "search": reverse("asset-search", request=request, format=format),
        "backtest": reverse("backtest-accuracy", request=request, format=format),
        "alerts": reverse("alert-rule-list", request=request, format=format),
        "webhooks": reverse("webhook-list", request=request, format=format),
        "usage": reverse("api-usage", request=request, format=format),
    })


def asset_search_html_view(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return render(request, "partials/search_results.html", {"results": []})

    assets = (
        Asset.objects
        .filter(Q(name__icontains=q) | Q(symbol__icontains=q))
        .filter(is_active=True)[:10]
    )
    return render(request, "partials/search_results.html", {"results": assets})


# ---------------------------------------------------------------------------
# HTML views (for HTMX-powered frontend)
# ---------------------------------------------------------------------------

SECTOR_CHOICES = dict(Asset.Sector.choices)


def _get_latest_snapshot(asset):
    return asset.market_snapshots.order_by("-observed_at").first()


def _get_latest_score(asset, model_name):
    return asset.score_snapshots.filter(model_name=model_name).order_by("-computed_at").first()


def _fmt(val, prefix="$", decimals=2):
    if val is None:
        return "-"
    d = Decimal(str(val))
    if d >= Decimal("1_000_000_000"):
        return f"{prefix}{d / Decimal('1_000_000_000'):,.{decimals}f}B"
    if d >= Decimal("1_000_000"):
        return f"{prefix}{d / Decimal('1_000_000'):,.{decimals}f}M"
    if d >= Decimal("1_000"):
        return f"{prefix}{d:,.{decimals}f}"
    return f"{prefix}{d:.{max(decimals, 4)}f}"


def dashboard_view(request):
    total_assets = Asset.objects.filter(is_active=True).count()
    total_snapshots = MarketSnapshot.objects.count()
    total_scores = ScoreSnapshot.objects.count()
    total_protocols = Protocol.objects.count()

    sector_counts = (
        Asset.objects
        .filter(is_active=True, sector__isnull=False)
        .values("sector")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    sector_data = [(row["sector"], SECTOR_CHOICES.get(row["sector"], row["sector"]), row["count"]) for row in sector_counts]

    top_10x = (
        ScoreSnapshot.objects
        .filter(model_name="10x_potential")
        .select_related("asset")
        .order_by("-score")[:5]
    )
    top_momentum = (
        ScoreSnapshot.objects
        .filter(model_name="momentum")
        .select_related("asset")
        .order_by("-score")[:5]
    )
    top_undervalued = (
        ScoreSnapshot.objects
        .filter(model_name="undervaluation")
        .select_related("asset")
        .order_by("-score")[:5]
    )

    latest_jobs = DataIngestionJob.objects.order_by("-started_at")[:5]

    return render(request, "core/dashboard.html", {
        "total_assets": total_assets,
        "total_snapshots": total_snapshots,
        "total_scores": total_scores,
        "total_protocols": total_protocols,
        "sector_data": sector_data,
        "top_10x": top_10x,
        "top_momentum": top_momentum,
        "top_undervalued": top_undervalued,
        "latest_jobs": latest_jobs,
    })


def asset_list_view(request):
    q = request.GET.get("search", "").strip()
    sector = request.GET.get("sector", "")
    sort = request.GET.get("sort", "-market_cap_usd")

    assets = Asset.objects.filter(is_active=True).prefetch_related(
        "market_snapshots", "score_snapshots",
    )

    if q:
        assets = assets.filter(Q(name__icontains=q) | Q(symbol__icontains=q))
    if sector:
        assets = assets.filter(sector=sector)

    # Annotate with latest market cap for sorting
    latest_snap_subq = MarketSnapshot.objects.filter(
        asset=OuterRef("pk")
    ).order_by("-observed_at").values("market_cap_usd")[:1]
    assets = assets.annotate(latest_mc=Subquery(latest_snap_subq))

    sort_map = {
        "name": "name", "-name": "-name",
        "symbol": "symbol", "-symbol": "-symbol",
        "market_cap_usd": "latest_mc", "-market_cap_usd": "-latest_mc",
        "sector": "sector", "-sector": "-sector",
    }
    assets = assets.order_by(sort_map.get(sort, "-latest_mc"))

    # Pagination
    page_size = 50
    page = int(request.GET.get("page", 1))
    total = assets.count()
    total_pages = (total + page_size - 1) // page_size
    assets = assets[(page - 1) * page_size: page * page_size]

    # Attach latest snapshot and scores to each asset
    asset_data = []
    for asset in assets:
        snap = _get_latest_snapshot(asset)
        scores = {}
        for sn in asset.score_snapshots.select_related().all()[:8]:
            if sn.model_name not in scores:
                scores[sn.model_name] = sn
        asset_data.append({
            "asset": asset,
            "snap": snap,
            "scores": scores,
        })

    return render(request, "core/asset_list.html", {
        "asset_data": asset_data,
        "query": q,
        "selected_sector": sector,
        "sort": sort,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "sectors": Asset.Sector.choices,
    })


def asset_detail_view(request, asset_id):
    asset = get_object_or_404(
        Asset.objects.prefetch_related(
            "market_snapshots", "score_snapshots", "score_snapshots__score_factors",
            "protocols", "protocols__tvl_snapshots", "protocols__fee_snapshots",
            "protocols__revenue_snapshots",
            "catalysts", "developer_activity_snapshots",
            "contract_addresses__holder_snapshots",
            "contract_addresses__blockchain",
        ),
        id=asset_id,
    )

    latest_snap = _get_latest_snapshot(asset)
    scores = {}
    for sn in asset.score_snapshots.all():
        if sn.model_name not in scores:
            scores[sn.model_name] = sn

    protocols = asset.protocols.all()
    catalysts = asset.catalysts.all()
    dev = asset.developer_activity_snapshots.first()
    holders = []
    for ca in asset.contract_addresses.all():
        hs = ca.holder_snapshots.first()
        if hs:
            holders.append({"chain": ca.blockchain.slug, "address": ca.address, "snapshot": hs})

    return render(request, "core/asset_detail.html", {
        "asset": asset,
        "snap": latest_snap,
        "scores": scores,
        "protocols": protocols,
        "catalysts": catalysts,
        "dev": dev,
        "holders": holders,
        "sector_display": asset.get_sector_display() if asset.sector else "Unclassified",
    })


def rankings_view(request):
    model_name = request.GET.get("model", "10x_potential")
    min_conf = request.GET.get("min_confidence", "0")

    latest = (
        ScoreSnapshot.objects
        .filter(model_name=model_name)
        .values("asset")
        .annotate(latest_id=Max("id"))
    )
    scores = (
        ScoreSnapshot.objects
        .filter(id__in=Subquery(latest.values("latest_id")))
        .select_related("asset")
        .prefetch_related("score_factors")
    )

    try:
        min_conf_val = Decimal(min_conf)
        scores = scores.filter(data_confidence__gte=min_conf_val)
    except Exception:
        pass

    scores = scores.order_by("-score")

    return render(request, "core/rankings.html", {
        "scores": scores[:100],
        "model_name": model_name,
        "min_confidence": min_conf,
        "model_choices": ScoreSnapshot.ModelName.choices,
    })


def protocols_view(request):
    q = request.GET.get("search", "").strip()
    category = request.GET.get("category", "")

    protos = Protocol.objects.filter(is_active=True).select_related("asset").prefetch_related(
        "tvl_snapshots", "fee_snapshots", "revenue_snapshots",
    )

    if q:
        protos = protos.filter(Q(name__icontains=q) | Q(slug__icontains=q))
    if category:
        protos = protos.filter(category__icontains=category)

    protos = protos.annotate(
        latest_tvl=Subquery(
            TVLSnapshot.objects.filter(protocol=OuterRef("pk")).order_by("-observed_at").values("tvl_usd")[:1]
        )
    ).order_by("-latest_tvl")

    categories = (
        Protocol.objects.filter(is_active=True)
        .values_list("category", flat=True)
        .distinct()
    )

    return render(request, "core/protocols.html", {
        "protocols": protos[:100],
        "query": q,
        "selected_category": category,
        "categories": sorted([c for c in categories if c]),
    })
