from django.urls import path
from . import views

app_name = "drafts"
urlpatterns = [
    path("", views.DraftListView.as_view(), name="list"),
    path("new/", views.DraftCreateView.as_view(), name="create"),
    path("<int:pk>/", views.DraftDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.DraftUpdateView.as_view(), name="edit"),
    path("<int:pk>/enrichment/", views.EnrichmentView.as_view(), name="enrichment"),
    path("<int:pk>/enrichment/run/", views.run_enrichment, name="enrichment_run"),
    path("<int:pk>/enrichment/<int:enrichment_pk>/apply/", views.apply_enrichment, name="enrichment_apply"),
    path("<int:pk>/preflight/", views.DraftPreflightView.as_view(), name="preflight"),
    path("<int:pk>/commercial-publish/", views.CommercialPublicationConfirmView.as_view(), name="commercial_publish_confirm"),
    path("<int:pk>/commercial-publish/execute/", views.publish_commercial_draft, name="commercial_publish_execute"),
    path("<int:pk>/test-publish/", views.TestPublicationConfirmView.as_view(), name="test_publish_confirm"),
    path("<int:pk>/test-publish/execute/", views.publish_test_draft, name="test_publish_execute"),
    path("metadata/category-suggestions/", views.category_suggestions, name="category_suggestions"),
    path("metadata/category-attributes/", views.category_attributes, name="category_attributes"),
    path("mercadolibre/connection/", views.ConnectionStatusView.as_view(), name="connection"),
    path("mercadolibre/diagnostic/", views.IntegrationDiagnosticView.as_view(), name="diagnostic"),
    path("mercadolibre/connect/", views.start_connection, name="connect"),
    path("mercadolibre/callback/", views.oauth_callback, name="oauth_callback"),
    path("mercadolibre/disconnect/", views.disconnect, name="disconnect"),
]
