from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponseForbidden
from django.urls import include, path, re_path
from django.views import defaults as default_views

from autocomplete import HTMXAutoComplete

# django_prometheus exposes an exports view used for secure metrics
from django_prometheus import exports

from . import views
from ._views.api_clients import (
    manage_api_client_form,
    manage_api_clients,
    rotate_api_client_secret,
)


def secure_metrics(request):
    ip = request.META.get("REMOTE_ADDR", "")
    if (
        ip.startswith("127.")
        or ip.startswith("10.")
        or ip.startswith("172.")
        or ip.startswith("100.")
    ):
        return exports.ExportToDjangoView(request)
    return HttpResponseForbidden("Internal metrics access only.")


urlpatterns = [
    path("healthz/", views.health_check, name="health_check"),
    path("", views.index, name="index"),
    path("api/v1/", include("otto.api.urls")),
    re_path(r"^upload/", include("django_file_form.urls")),
    path("welcome/", views.welcome, name="welcome"),
    path("dev/browser-login/", views.browser_test_login, name="browser_test_login"),
    # AC-2: Entra Integration Helper App Configuration
    # AC-14: Login Page Accessibility
    path("azure_auth/login", views.login, name="login"),
    path("azure_auth/", include("azure_auth.urls")),
    # Wrap the azure callback to manage session guard and friendly errors
    # Support both with and without trailing slash to avoid redirect issues with long OAuth URLs
    path("accounts/login/callback", views.azure_callback, name="callback"),
    path("accounts/login/callback/", views.azure_callback),
    # path("admin/", admin.site.urls), # Do not expose the admin site in production
    path("librarian/", include("librarian.urls")),
    path("laws/", include("laws.urls")),
    path("text_extractor/", include("text_extractor.urls")),
    path("manage_banner/", views.manage_banner, name="manage_banner"),
    path("user_management/", views.manage_users, name="manage_users"),
    path("user_management/merge/", views.manage_user_merge, name="manage_user_merge"),
    path(
        "user_management/external_tool_approvals/",
        views.manage_external_tool_approvals,
        name="manage_external_tool_approvals",
    ),
    path(
        "user_management/api_clients/",
        manage_api_clients,
        name="manage_api_clients",
    ),
    path(
        "user_management/api_clients/form/",
        manage_api_client_form,
        name="manage_api_client_form",
    ),
    path(
        "user_management/api_clients/form/<int:client_id>/",
        manage_api_client_form,
        name="manage_api_client_form_edit",
    ),
    path(
        "user_management/api_clients/<int:client_id>/rotate_secret/",
        rotate_api_client_secret,
        name="rotate_api_client_secret",
    ),
    path("user_management/data/", views.manage_users_data, name="manage_users_data"),
    path("user_management/form/", views.manage_users_form, name="manage_users_form"),
    path(
        "user_management/form/<user_id>/",
        views.manage_users_form,
        name="manage_users_form",
    ),
    path(
        "user_management/form/<str:user_ids>/",
        views.manage_users_form,
        name="manage_users_form",
    ),
    path("user_management/upload/", views.manage_users_upload, name="upload_users"),
    path(
        "user_management/download/", views.manage_users_download, name="download_users"
    ),
    path(
        "user_management/cost_groups/",
        views.manage_cost_groups,
        name="manage_cost_groups",
    ),
    path(
        "user_management/cost_groups/form/",
        views.manage_cost_groups_form,
        name="manage_cost_groups_form",
    ),
    path(
        "user_management/cost_groups/form/<cost_group_id>/",
        views.manage_cost_groups_form,
        name="manage_cost_groups_form",
    ),
    path("user_management/costs/", views.cost_dashboard, name="cost_dashboard"),
    path("user_management/usage/", views.usage_dashboard, name="usage_dashboard"),
    path("load_test/enable", views.enable_load_testing, name="enable_load_testing"),
    path("load_test/disable", views.disable_load_testing, name="disable_load_testing"),
    path("load_test/", views.load_test, name="load_test"),
    path("user_cost/", views.user_cost, name="user_cost"),
    path("select_cost_group/", views.select_cost_group, name="select_cost_group"),
    path("clear_cost_group/", views.clear_cost_group, name="clear_cost_group"),
    path(
        "set_default_ai_assistant/",
        views.set_default_ai_assistant,
        name="set_default_ai_assistant",
    ),
    path("extend_session/", views.extend_session, name="extend_session"),
    path("terms_of_use/", views.terms_of_use, name="terms_of_use"),
    path(
        "frequently_asked_questions/",
        views.frequently_asked_questions,
        name="frequently_asked_questions",
    ),
    path(
        "user_management/mark_tour_completed/<str:tour_name>/",
        views.mark_tour_completed,
        name="mark_tour_completed",
    ),
    path(
        "user_management/reset_completion_flags/",
        views.reset_completion_flags,
        name="reset_completion_flags",
    ),
    path("feedback/", views.feedback_message, name="user_feedback"),
    path("feedback/<int:message_id>/", views.feedback_message, name="user_feedback"),
    path(
        "user_management/feedback", views.feedback_dashboard, name="feedback_dashboard"
    ),
    path(
        "user_management/feedback/<int:page_number>",
        views.feedback_dashboard,
        name="feedback_dashboard",
    ),
    path("user_management/feedback/list", views.feedback_list, name="feedback_list"),
    path(
        "user_management/feedback/list/<int:page_number>",
        views.feedback_list,
        name="feedback_list",
    ),
    path("user_management/feedback/stats", views.feedback_stats, name="feedback_stats"),
    path(
        "user_management/feedback/<int:feedback_id>/<str:form_type>",
        views.feedback_dashboard_update,
        name="feedback_dashboard_update",
    ),
    path(
        "user_management/feedback/download",
        views.feedback_download,
        name="feedback_download",
    ),
    path(
        "user_management/modify_variables_modal/",
        views.modify_variables_modal,
        name="modify_variables_modal",
    ),
    path(
        "user_management/modify_variables/",
        views.modify_variables_update,
        name="modify_variables_update",
    ),
    path("user_management/blocked_urls", views.list_blocked_urls, name="blocked_urls"),
    path(
        "user_management/skill_tags/",
        views.manage_skill_tags,
        name="manage_skill_tags",
    ),
    path(
        "user_management/skill_tags/add/",
        views.add_skill_tag,
        name="add_skill_tag",
    ),
    path(
        "user_management/skill_tags/<int:tag_id>/edit/",
        views.edit_skill_tag,
        name="edit_skill_tag",
    ),
    path(
        "user_management/skill_tags/merge/",
        views.merge_skill_tags,
        name="merge_skill_tags",
    ),
    path(
        "user_management/skill_tags/<int:tag_id>/delete/",
        views.delete_skill_tag,
        name="delete_skill_tag",
    ),
    path(
        "notifications/<int:notification_id>/", views.notification, name="notification"
    ),
    path("notifications/", views.notifications, name="notifications"),
    # Team management
    path("teams/", views.manage_teams, name="manage_teams"),
    path("teams/form/", views.manage_teams_form, name="manage_teams_form"),
    path(
        "teams/form/<int:team_id>/",
        views.manage_teams_form,
        name="manage_teams_form_edit",
    ),
    path(
        "teams/<int:team_id>/delete/",
        views.delete_team,
        name="delete_team",
    ),
    path("i18n/", include("django.conf.urls.i18n")),
    path("chat/", include("chat.urls")),
    path("chat_next/", include("chat_next.urls")),
    # exposes /metrics endpoint, accessible by anonymous users, restricted by ip addresses. See metrics/web.config and IIS's IP Address and Domain Restrictions
    # path("metrics/", secure_metrics),
    path("", include("django_prometheus.urls")),
    path("translate/", include("translate.urls")),
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
