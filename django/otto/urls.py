from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views import defaults as default_views

from autocomplete import HTMXAutoComplete
from azure_auth.views import azure_auth_callback

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("search/", views.topnav_search_inner, name="search_inner"),
    path("welcome/", views.welcome, name="welcome"),
    # AC-2: Entra Integration Helper App Configuration
    # AC-14: Login Page Accessibility
    path("azure_auth/login", views.login, name="login"),
    path("azure_auth/", include("azure_auth.urls")),
    path("accounts/login/callback/", azure_auth_callback, name="callback"),
    # path("admin/", admin.site.urls),
    path("librarian/", include("librarian.urls")),
    path("laws/", include("laws.urls")),
    path("case_prep/", include("case_prep.urls")),
    path("text_extractor/", include("text_extractor.urls")),
    path("template_wizard/", include("template_wizard.urls")),
    path("user_management/", views.manage_users, name="manage_users"),
    path("user_management/form/", views.manage_users_form, name="manage_users_form"),
    path(
        "user_management/form/<user_id>/",
        views.manage_users_form,
        name="manage_users_form",
    ),
    path("user_management/upload/", views.manage_users_upload, name="upload_users"),
    path(
        "user_management/download/", views.manage_users_download, name="download_users"
    ),
    path("user_management/pilots/", views.manage_pilots, name="manage_pilots"),
    path(
        "user_management/pilots/form/",
        views.manage_pilots_form,
        name="manage_pilots_form",
    ),
    path(
        "user_management/pilots/form/<pilot_id>/",
        views.manage_pilots_form,
        name="manage_pilots_form",
    ),
    path("user_management/costs/", views.cost_dashboard, name="cost_dashboard"),
    path("accept_terms/", views.accept_terms, name="accept_terms"),
    path("feedback/", views.message_feedback, name="user_feedback"),
    path("feedback/<message_id>/", views.message_feedback, name="user_feedback"),
    path("feedback_success/", views.feedback_success, name="feedback_success"),
    path(
        "notifications/<int:notification_id>/", views.notification, name="notification"
    ),
    path("notifications/", views.notifications, name="notifications"),
    path("i18n/", include("django.conf.urls.i18n")),
    path("chat/", include("chat.urls")),
    # exposes /metrics endpoint, accessible by anonymous users, restricted by ip addresses. See metrics/web.config and IIS's IP Address and Domain Restrictions
    path("", include("django_prometheus.urls")),
    *HTMXAutoComplete.url_dispatcher("ac"),
] + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

if settings.DEBUG_TOOLBAR:
    urlpatterns.append(
        path("__debug__/", include("debug_toolbar.urls")),
    )

if settings.DEBUG:
    urlpatterns += [
        path(
            "400/",
            default_views.bad_request,
            kwargs={"exception": Exception("Bad Request")},
        ),
        path(
            "403/",
            default_views.permission_denied,
            kwargs={"exception": Exception("Permission Denied")},
        ),
        path(
            "404/",
            default_views.page_not_found,
            kwargs={"exception": Exception("Page Not Found")},
        ),
        path("500/", default_views.server_error),
    ]
