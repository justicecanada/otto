from django.urls import path

from otto.api.docs import OttoApiRedocView, OttoApiSchemaView, OttoApiSwaggerView

from . import views

urlpatterns = [
    path(
        "",
        OttoApiSwaggerView.as_view(url_name="api_schema"),
        name="api_docs",
    ),
    path("schema/", OttoApiSchemaView.as_view(), name="api_schema"),
    path(
        "redoc/",
        OttoApiRedocView.as_view(url_name="api_schema"),
        name="api_redoc",
    ),
    path(
        "reporting/user-activity/summary/",
        views.UserActivitySummaryView.as_view(),
        name="api_user_activity_summary",
    ),
    path(
        "reporting/user-activity/users/",
        views.UserActivityUsersView.as_view(),
        name="api_user_activity_users",
    ),
]
