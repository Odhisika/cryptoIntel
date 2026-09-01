from django.urls import path

from . import views
from .payments import paystack_webhook
from .tgbot import telegram_webhook

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
    path("v1/backtest/", views.backtest_accuracy_view, name="backtest-accuracy"),
    path("v1/alerts/rules/", views.AlertRuleListCreateView.as_view(), name="alert-rule-list"),
    path("v1/alerts/rules/<uuid:id>/", views.AlertRuleDetailView.as_view(), name="alert-rule-detail"),
    path("v1/alerts/history/", views.AlertEventListView.as_view(), name="alert-history"),
    path("v1/webhooks/", views.WebhookSubscriptionListCreateView.as_view(), name="webhook-list"),
    path("v1/webhooks/<uuid:id>/", views.WebhookSubscriptionDetailView.as_view(), name="webhook-detail"),
    path("v1/usage/", views.ApiUsageView.as_view(), name="api-usage"),
    path("v1/telegram/verify/", views.telegram_bind_view, name="telegram-bind"),
    path("webhooks/paystack/", paystack_webhook, name="paystack-webhook"),
    path("webhooks/telegram/", telegram_webhook, name="telegram-webhook"),
]
