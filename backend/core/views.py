from decimal import Decimal

from django.db.models import Q, Count, F, OuterRef, Subquery, Max, Value
from django.db.models.functions import Coalesce
from django.shortcuts import render, get_object_or_404
from rest_framework import generics
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.reverse import reverse

from .models import (
    Asset, MarketSnapshot, ScoreSnapshot, ScoreFactor,
    Protocol, TVLSnapshot, Catalyst, DataIngestionJob, DataQualityIssue,
)
from .serializers import (
    AssetListSerializer, AssetDetailSerializer,
    ScoreSnapshotSerializer, ProtocolSerializer,
    CatalystSerializer, IngestionJobSerializer,
    DataQualityIssueSerializer,
)


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
        "protocols": reverse("protocol-list", request=request, format=format),
        "catalysts": reverse("catalyst-list", request=request, format=format),
        "dashboard": reverse("dashboard-stats", request=request, format=format),
        "search": reverse("asset-search", request=request, format=format),
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
