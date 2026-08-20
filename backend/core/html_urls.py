from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("assets/", views.asset_list_view, name="asset-list"),
    path("assets/<uuid:asset_id>/", views.asset_detail_view, name="asset-detail"),
    path("rankings/", views.rankings_view, name="rankings"),
    path("protocols/", views.protocols_view, name="protocols"),
    path("api/search/", views.asset_search_html_view, name="api-search"),
]
