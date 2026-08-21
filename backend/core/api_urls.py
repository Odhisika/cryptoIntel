from django.urls import path

from . import views
from .payments import paystack_webhook

app_name = "api"

urlpatterns = [
    path("v1/", views.api_root, name="api-root"),
    path("v1/assets/", views.AssetListView.as_view(), name="asset-list"),
    path("v1/assets/<uuid:id>/", views.AssetDetailView.as_view(), name="asset-detail"),
    path("v1/assets/search/", views.asset_search_view, name="asset-search"),
    path("v1/tiers/", views.tier_summary_view, name="tier-summary"),
    path("v1/scores/ranking/", views.ScoreRankingView.as_view(), name="score-ranking"),
    path("v1/protocols/", views.ProtocolListView.as_view(), name="protocol-list"),
    path("v1/catalysts/", views.CatalystListView.as_view(), name="catalyst-list"),
    path("v1/dashboard/stats/", views.dashboard_stats_view, name="dashboard-stats"),
    path("webhooks/paystack/", paystack_webhook, name="paystack-webhook"),
]
