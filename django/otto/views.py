import csv
import io
import math
import os
import time
import uuid
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth.models import Group
from django.contrib.postgres.aggregates import StringAgg
from django.core.cache import cache
from django.core.exceptions import (
    DisallowedRedirect,
    ObjectDoesNotExist,
    ValidationError,
)
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import connection, models
from django.db.models import OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Cast, Coalesce
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseServerError,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import check_for_language
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from azure_auth.exceptions import TokenError
from azure_auth.views import azure_auth_callback as _azure_auth_callback
from azure_auth.views import azure_auth_login as azure_auth_login
from chat_next.models import ExternalToolApprovalLog, SkillTag
from data_fetcher import cache_within_request
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from otto.browser_test_auth import (
    browser_test_personas,
    ensure_browser_test_user,
    seed_browser_test_users,
)
from otto.forms import (
    CostDashboardForm,
    CostGroupForm,
    CostGroupSelectionForm,
    FeedbackForm,
    FeedbackMetadataForm,
    FeedbackNoteForm,
    OttoStatusForm,
    TeamForm,
    UsageDashboardForm,
    UserGroupForm,
    UserMergeForm,
)
from otto.models import (
    CHAT_TYPE_CHOICES,
    COUNT_TYPE_CHOICES,
    FEATURE_CHOICES,
    BlockedURL,
    Cost,
    CostGroup,
    CostType,
    Feedback,
    OttoStatus,
    Team,
    TeamMembership,
)
from otto.user_merge import UserMergeError, build_user_merge_preview, merge_users
from otto.utils.common import (
    cad_cost,
    display_cad_cost,
    get_tld_extractor,
    robust_redirect,
)
from otto.utils.decorators import permission_required
from otto.utils.usage_dashboard_utils import (
    aggregate_counts,
    calculate_aggregated_dashboard_number,
    filter_group_count_types,
)

from chat.llm import OttoLLM
from librarian.models import DataSource, Document, Library, SavedFile

logger = get_logger(__name__)

User = get_user_model()

MANAGE_USERS_DEFAULT_VISIBLE_SEARCH_FIELDS = {
    "upn",
    "last_login",
    "cost_30_days",
    "roles",
    "cost_groups",
    "teams",
}

MANAGE_USERS_ALLOWED_SEARCH_FIELDS = MANAGE_USERS_DEFAULT_VISIBLE_SEARCH_FIELDS | {
    "entra_status",
    "job_title",
    "preferred_language",
    "cost_7_days",
    "cost_all_time",
}


def _user_cost_subquery(*, start_date=None):
    cost_queryset = Cost.objects.filter(user=OuterRef("pk"))
    if start_date is not None:
        cost_queryset = cost_queryset.filter(date_incurred__gte=start_date)
    return cost_queryset.values("user").annotate(total=Sum("usd_cost")).values("total")


def _parse_manage_users_visible_search_fields(request: HttpRequest) -> set[str]:
    raw_value = (request.GET.get("visible_search_fields", "") or "").strip()
    if not raw_value:
        return set(MANAGE_USERS_DEFAULT_VISIBLE_SEARCH_FIELDS)

    parsed_fields = {
        field.strip()
        for field in raw_value.split(",")
        if field.strip() in MANAGE_USERS_ALLOWED_SEARCH_FIELDS
    }
    return parsed_fields or set(MANAGE_USERS_DEFAULT_VISIBLE_SEARCH_FIELDS)


def _normalize_next_url(request: HttpRequest, next_url: str | None = None) -> str:
    candidate = next_url or request.GET.get("next") or request.POST.get("next") or "/"
    if not url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return "/"
    return candidate


def _get_browser_test_persona(persona_slug: str | None = None) -> dict[str, object]:
    default_slug = "first_admin"
    selected_slug = persona_slug or default_slug

    for persona in browser_test_personas():
        if persona["slug"] == selected_slug:
            return persona

    raise Http404()


def _browser_test_auth_context(
    request: HttpRequest, next_url: str | None = None
) -> dict[str, object]:
    if settings.BROWSER_TEST_AUTH_ENABLED and not settings.IS_RUNNING_TESTS:
        seed_browser_test_users()

    personas = browser_test_personas()
    return {
        "browser_test_auth_enabled": settings.BROWSER_TEST_AUTH_ENABLED,
        "browser_test_auth_next_url": _normalize_next_url(request, next_url),
        "browser_test_auth_personas": personas,
    }


def _set_browser_test_session(request: HttpRequest, user) -> None:
    expiry_timestamp = int(time.time()) + settings.SESSION_COOKIE_AGE
    display_name = getattr(user, "full_name", None) or user.upn
    request.session["browser_test_auth"] = True
    request.session["id_token_claims"] = {
        "exp": expiry_timestamp,
        "iat": int(time.time()),
        "login_hint": user.upn,
        "preferred_username": user.upn,
        "email": user.email,
        "name": display_name,
        "oid": user.oid,
    }
    request.session.pop("token_cache", None)
    request.session.pop("auth_flow", None)
    request.session.set_expiry(settings.SESSION_COOKIE_AGE)


def health_check(request):
    return JsonResponse({"status": "ok"})


def welcome(request):
    # Bilingual landing page with login button
    request.session["from_welcome"] = True
    next_url = _normalize_next_url(request)
    return render(
        request,
        "welcome.html",
        {
            "next_url": next_url,
            **_browser_test_auth_context(request, next_url=next_url),
        },
    )


def login(request: HttpRequest):
    # Wraps azure_auth login to allow for language selection
    # Guard against concurrent login flows overwriting PKCE code_verifier
    # Also auto-reset the guard if the previous flow seems stale (tab closed or abandoned)
    guard = request.session.get("auth_in_progress")

    # Do not block new login attempts; if a prior flow exists, inform the user and replace it
    if guard:
        # Clear previous guard so we can start a fresh flow immediately (no message here).
        request.session["auth_in_progress"] = False
        request.session.pop("auth_started_at", None)

    lang_code = request.GET.get("lang")
    # Mark flow started before redirecting to Azure
    request.session["auth_in_progress"] = True
    request.session["auth_started_at"] = timezone.now().isoformat()
    try:
        response = azure_auth_login(request)
    except Exception as e:
        # If starting the Azure flow fails, clear guard and show a clean page in this tab
        request.session["auth_in_progress"] = False
        request.session.pop("auth_started_at", None)
        logger.exception("Error starting Azure login flow", error=str(e))
        return render(
            request,
            "auth/login_issue.html",
            {
                "hide_breadcrumbs": True,
                "hide_nav": True,
                **_browser_test_auth_context(request),
            },
            status=200,
        )
    # See django.views.i18n.set_language for the source of this code
    if lang_code and check_for_language(lang_code):
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME,
            lang_code,
            max_age=settings.LANGUAGE_COOKIE_AGE,
            path=settings.LANGUAGE_COOKIE_PATH,
            domain=settings.LANGUAGE_COOKIE_DOMAIN,
            secure=settings.LANGUAGE_COOKIE_SECURE,
            httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
            samesite=settings.LANGUAGE_COOKIE_SAMESITE,
        )
    return response


def azure_callback(request: HttpRequest):
    """
    Wrapper around azure_auth_callback to clear concurrent-login guard and
    handle PKCE mismatch gracefully (AADSTS501481).
    """
    try:
        response = _azure_auth_callback(request)
        return response
    except TokenError as e:
        # Handle PKCE/code_verifier mismatch errors (Azure AADSTS501481)
        msg = str(e)
        if "AADSTS501481" in msg or "Code_Verifier" in msg:
            request.session["auth_in_progress"] = False
            request.session.pop("auth_started_at", None)
            return render(
                request,
                "auth/login_issue.html",
                {
                    "hide_breadcrumbs": True,
                    "hide_nav": True,
                    **_browser_test_auth_context(request),
                },
                status=200,
            )
        # Not the PKCE mismatch we want to handle — re-raise so genuine errors surface
        raise
    except ValueError as e:
        # MSAL raises ValueError("state missing from auth_code_flow") when the
        # 'state' value isn't present in the stored auth_code_flow (stale/old tab).
        msg = str(e)
        if "state missing from auth_code_flow" in msg or "state missing" in msg:
            # Clear the guard and show the friendly page only for this specific case
            request.session["auth_in_progress"] = False
            request.session.pop("auth_started_at", None)
            return render(
                request,
                "welcome.html",
                {
                    "hide_breadcrumbs": True,
                    "hide_nav": True,
                    "next_url": "/",
                    **_browser_test_auth_context(request),
                },
                status=200,
            )
        # Not the specific state-missing error we want to handle — re-raise
        raise
    except DisallowedRedirect:
        # Django 5.2+ rejects redirects exceeding 2048 characters.
        # This can happen if the OAuth callback URL or next parameter is too long.
        # Log and redirect to homepage instead.
        logger.warning(
            "DisallowedRedirect during OAuth callback - redirect URL exceeded 2048 chars"
        )
        request.session["auth_in_progress"] = False
        request.session.pop("auth_started_at", None)
        return redirect(settings.LOGIN_REDIRECT_URL)
    finally:
        # Clear guard after callback regardless of outcome
        request.session["auth_in_progress"] = False
        request.session.pop("auth_started_at", None)


@require_POST
def browser_test_login(request: HttpRequest):
    if not settings.BROWSER_TEST_AUTH_ENABLED:
        raise Http404()

    next_url = _normalize_next_url(request)
    persona = _get_browser_test_persona(request.POST.get("persona"))
    user = ensure_browser_test_user(persona)
    auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    _set_browser_test_session(request, user)

    request.session["auth_in_progress"] = False
    request.session.pop("auth_started_at", None)
    if next_url == "/":
        request.session["from_welcome"] = True

    messages.info(
        request,
        _("Signed in using local browser test mode as %(persona)s.")
        % {"persona": persona["label"]},
    )
    return redirect(next_url)


def index(request):
    # Determine if the tour should be forced
    force_tour = not request.user.homepage_tour_completed
    tour_skippable = request.user.is_admin or request.user.homepage_tour_completed

    # If an existing user is logging in and doesn't need the tour, redirect to the AI assistant
    if request.session.pop("from_welcome", False) and not force_tour:
        return redirect(request.user.default_ai_assistant_route)

    # Otherwise, show the homepage (with or without the tour)
    context = {
        "hide_breadcrumbs": True,
        "has_tour": True,
        "force_tour": force_tour,
        "tour_skippable": tour_skippable,
    }
    return render(request, "index.html", context)


@require_POST
def set_default_ai_assistant(request):
    assistant = request.POST.get("assistant")
    if assistant not in {"chat", "chat_next"}:
        return HttpResponse(status=400)

    if assistant == "chat_next" and not request.user.has_perm(
        "otto.can_access_chat_next"
    ):
        return HttpResponse(status=400)

    request.user.default_ai_assistant = assistant
    request.user.save(update_fields=["default_ai_assistant"])

    if request.headers.get(
        "X-Requested-With"
    ) == "XMLHttpRequest" or request.headers.get("HX-Request"):
        return HttpResponse(status=204)

    redirect_to = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"
    if not url_has_allowed_host_and_scheme(
        redirect_to,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        redirect_to = "/"
    return redirect(redirect_to)


def frequently_asked_questions(request):
    return render(
        request,
        "frequently_asked_questions.html",
        {
            "hide_breadcrumbs": True,
        },
    )


def terms_of_use(request):
    if request.method == "POST":
        logger.info("Terms of conditions were accepted")
        request.user.accepted_terms_date = timezone.now()
        request.user.save()

        redirect_url = request.POST.get("redirect_url") or "/"
        return redirect(redirect_url)

    redirect_url = request.GET.get("next", "/")

    return render(
        request,
        "terms_of_use.html",
        {
            "hide_breadcrumbs": True,
            "last_updated": OttoStatus.objects.singleton().terms_last_updated,
            "redirect_url": redirect_url,
        },
    )


def feedback_message(request: HttpRequest, message_id=None, is_chat_next=False):
    if message_id == "None":
        message_id = None
    if request.method == "POST":
        from django.contrib import messages

        from otto.utils.common import get_app_from_path

        is_chat_next = request.POST.get("chat_message_next", "") not in ["", "None"]
        form = FeedbackForm(
            request.user, message_id, request.POST, is_chat_next=is_chat_next
        )

        if form.is_valid():
            feedback_saved = form.save(commit=False)
            date_and_time = timezone.now().strftime("%Y%m%d-%H%M%S")
            feedback_saved.created_at = date_and_time
            if (
                feedback_saved.chat_message is None
                and feedback_saved.chat_message_next is None
            ):
                feedback_saved.app = get_app_from_path(feedback_saved.url_context)
            elif feedback_saved.chat_message_next is not None:
                # chat_next feedback - capture settings snapshot
                try:
                    from chat_next.models import Message as MessageNext

                    msg = feedback_saved.chat_message_next
                    if isinstance(msg, int):
                        msg = MessageNext.objects.get(id=msg)
                    chat = getattr(msg, "chat", None)
                    if chat:
                        chat_settings = getattr(chat, "settings", None)
                        if chat_settings:
                            snapshot = {
                                "app": "chat_next",
                                "chat_model": chat_settings.chat_model,
                                "chat_temperature": chat_settings.chat_temperature,
                                "chat_reasoning_effort": chat_settings.chat_reasoning_effort,
                                "chat_verbosity": chat_settings.chat_verbosity,
                                "chat_system_prompt": chat_settings.chat_system_prompt,
                                "chat_enabled_tools": chat_settings.chat_enabled_tools,
                                "chat_max_iterations": chat_settings.chat_max_iterations,
                                "chat_include_images": chat_settings.chat_include_images,
                                "chat_include_pdfs": chat_settings.chat_include_pdfs,
                                "user_display_name": chat_settings.user_display_name,
                                "send_name_to_model": chat_settings.send_name_to_model,
                                "job_description": chat_settings.job_description,
                                "global_instructions": chat_settings.global_instructions,
                            }
                            feedback_saved.preset_snapshot = snapshot
                except Exception as e:
                    logger.exception(
                        f"Failed to capture settings snapshot for chat_next feedback: {e}"
                    )
            else:
                # Capture the preset and options at the time feedback was submitted
                try:
                    from chat.models import Message

                    # feedback_saved.chat_message should be a Message instance when saved via ModelForm
                    msg = feedback_saved.chat_message
                    if isinstance(msg, int):
                        msg = Message.objects.get(id=msg)
                    chat = getattr(msg, "chat", None)
                    if chat:
                        # Link to the loaded preset (if any)
                        if getattr(chat, "loaded_preset", None):
                            feedback_saved.loaded_preset = chat.loaded_preset

                        # Serialize a compact snapshot of ChatOptions
                        options = getattr(chat, "options", None)
                        if options:
                            snapshot = {
                                "mode": options.mode,
                                "prompt": options.prompt,
                                "chat_model": options.chat_model,
                                "chat_temperature": options.chat_temperature,
                                "chat_reasoning_effort": options.chat_reasoning_effort,
                                "chat_verbosity": options.chat_verbosity,
                                "chat_system_prompt": options.chat_system_prompt,
                                "summarize_model": options.summarize_model,
                                "summarize_reasoning_effort": options.summarize_reasoning_effort,
                                "summarize_prompt": options.summarize_prompt,
                                "qa_model": options.qa_model,
                                "qa_topk": options.qa_topk,
                                "qa_scope": options.qa_scope,
                                "qa_mode": options.qa_mode,
                                "qa_process_mode": options.qa_process_mode,
                                "qa_pre_instructions": options.qa_pre_instructions,
                                "qa_post_instructions": options.qa_post_instructions,
                                "qa_library_id": options.qa_library.id,
                                "qa_system_prompt": options.qa_system_prompt
                                if options.qa_library
                                else None,
                                "qa_library_name": options.qa_library.name_en
                                if options.qa_library
                                else None,
                                "translate_language": options.translate_language,
                                "translate_model": options.translate_model,
                                "translate_prompt": options.translate_prompt,
                                "translate_glossary_filename": options.translate_glossary_filename,
                            }
                            feedback_saved.preset_snapshot = snapshot
                except Exception as e:
                    logger.exception(
                        f"Failed to capture preset snapshot for feedback: {e}"
                    )
            feedback_saved.save()
            messages.success(
                request,
                _("Feedback submitted successfully."),
            )
            return HttpResponse(status=200)
        else:
            messages.error(
                request,
                _("Error submitting feedback."),
            )
            return HttpResponse(status=200)
    else:
        form = FeedbackForm(request.user, message_id, is_chat_next=is_chat_next)
    return render(
        request,
        "components/feedback/feedback_modal_content.html",
        {
            "form": form,
            "message_id": message_id,
            "hide_breadcrumbs": True,
            "hide_nav": message_id is not None,
        },
    )


@permission_required("otto.manage_feedback")
def feedback_dashboard(request, page_number=None):
    if page_number is None:
        page_number = 1

    apps = Feedback.objects.values_list("app", flat=True).distinct()
    feedback_status_choices = Feedback.FEEDBACK_STATUS_CHOICES
    feedback_type_choices = Feedback.FEEDBACK_TYPE_CHOICES

    # Get users who have submitted feedback
    user_options = (
        Feedback.objects.exclude(created_by__isnull=True)
        .values_list("created_by__email", flat=True)
        .distinct()
        .order_by("created_by__email")
    )

    context = {
        "apps": apps,
        "feedback_status_choices": feedback_status_choices,
        "feedback_type_choices": feedback_type_choices,
        "current_page_number": page_number,
        "users": user_options,
    }

    return render(request, "feedback_dashboard.html", context)


@permission_required("otto.manage_feedback")
def feedback_stats(request):
    stats = Feedback.objects.get_feedback_stats()
    return render(
        request, "components/feedback/dashboard/feedback_stats.html", {"stats": stats}
    )


@permission_required("otto.manage_feedback")
def feedback_list(request, page_number=None):
    from django.core.paginator import Paginator

    feedback_messages = (
        Feedback.objects.all().select_related("created_by").order_by("-created_at")
    )

    # Only use GET for filters to ensure pagination works with query params
    feedback_type = request.GET.get("feedback_type")
    status = request.GET.get("status")
    app = request.GET.get("app")
    user = request.GET.get("user")

    if feedback_type and feedback_type != "all":
        feedback_messages = feedback_messages.filter(feedback_type=feedback_type)
    if status and status != "all":
        feedback_messages = feedback_messages.filter(status=status)
    if app and app != "all":
        feedback_messages = feedback_messages.filter(app=app)
    if user and user != "all":
        # Filter by user email
        feedback_messages = feedback_messages.filter(created_by__email=user)

    paginator = Paginator(feedback_messages, 10)
    try:
        page_number = int(page_number)
    except (TypeError, ValueError):
        page_number = 1
    page_obj = paginator.get_page(page_number)

    feedback_info = [
        {
            "feedback": f,
            "form": {
                "notes": FeedbackNoteForm(instance=f, auto_id=f"{f.id}_%s"),
                "metadata": FeedbackMetadataForm(instance=f, auto_id=f"{f.id}_%s"),
            },
        }
        for f in page_obj
    ]
    context = {
        "feedback_info": feedback_info,
        "page_obj": page_obj,
        "request": request,
    }
    return render(request, "components/feedback/dashboard/feedback_list.html", context)


@permission_required("otto.manage_feedback")
def feedback_dashboard_update(request, feedback_id, form_type):
    from django.template.loader import render_to_string

    feedback = Feedback.objects.get(id=feedback_id)

    if request.method == "POST":
        if form_type == "metadata":
            form = FeedbackMetadataForm(request.POST, instance=feedback)
        else:
            form = FeedbackNoteForm(request.POST, instance=feedback)
        if form.is_valid():
            form.cleaned_data["modified_by"] = request.user
            form.cleaned_data["modified_at"] = timezone.now()
            form.save()
            messages.success(
                request,
                _("Feedback updated successfully."),
            )
            badge_html = render_to_string(
                "components/feedback/dashboard/feedback_type_status.html",
                {"info": {"feedback": feedback}},
            )
            return HttpResponse(badge_html)
        else:
            messages.error(request, form.errors)
            return HttpResponse(str(form.errors), status=400)

    else:
        return HttpResponse(status=405)


@permission_required("otto.manage_feedback")
def feedback_download(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="otto_feedback.csv"'

    writer = csv.writer(response)
    writer.writerow(
        [
            "created_at",
            "user",
            "app",
            "message",
            "status",
            "type",
            "last_modified_by",
            "last_modified_on",
            "notes",
            "version",
            "url_context",
        ]
    )

    # Get all feedback messages from the row headers above
    # Use select_related to avoid N+1 queries when accessing created_by and modified_by
    for feedback in (
        Feedback.objects.all()
        .select_related("created_by", "modified_by")
        .order_by("-created_at")
    ):
        writer.writerow(
            [
                feedback.created_at,
                feedback.created_by,
                feedback.app,
                feedback.feedback_message,
                feedback.status,
                feedback.feedback_type,
                feedback.modified_by,
                feedback.modified_on,
                feedback.admin_notes,
                feedback.otto_version,
                feedback.url_context,
            ],
        )
    return response


def notification(request, notification_id):
    """
    For handling deleting of notifications
    """
    notification = request.user.notifications.get(id=notification_id)
    if request.method == "DELETE":
        notification.delete()
    no_more_notifications = not request.user.notifications.exists()
    logger.debug("no more notifications?", has_notifications=no_more_notifications)
    return notifications(request, hide=no_more_notifications)


def notifications(request, hide=False):
    """
    Updates the notifications badge and list of notifications
    e.g. on page load, after notification icon clicked, during polling, etc.
    """
    notifications = request.user.notifications.all().order_by("-created_at")
    return render(
        request,
        "components/notifications_update.html",
        {
            "notifications": notifications,
            # Expand the notifications dropdown if there are any errors
            "show_notifications": any(
                n for n in notifications if n.category == "error"
            ),
            "hide_notifications": hide,
        },
    )


# AC-3(7), AC-16, & AC-16(2): Allows authorized administrators to modify user groups and roles
@permission_required("otto.manage_users")
def manage_users(request):
    def invalid_manage_users_response(form, status=400):
        if request.headers.get("HX-Request"):
            return render(
                request,
                "components/user_roles_modal.html",
                {"form": form},
                status=status,
            )
        context = {
            "roles": Group.objects.all(),
            "form": form,
            "users_data_url": reverse("manage_users_data"),
        }
        return render(request, "manage_users.html", context, status=status)

    if request.method == "POST":
        form = UserGroupForm(request.POST)
        if form.is_valid():
            users = form.cleaned_data["upn"]
            groups = form.cleaned_data["group"]
            cost_groups = form.cleaned_data["cost_group"]
            admin_teams = form.cleaned_data["teams_admin"]
            member_teams = form.cleaned_data["teams_member"]
            selected_user_ids = {user.id for user in users}
            desired_admin_team_ids = {team.id for team in admin_teams}
            affected_team_ids = set(
                TeamMembership.objects.filter(user__in=users).values_list(
                    "team_id", flat=True
                )
            )
            affected_team_ids.update(desired_admin_team_ids)

            for team_id in affected_team_ids:
                remaining_admin_ids = set(
                    TeamMembership.objects.filter(team_id=team_id, role="admin")
                    .exclude(user_id__in=selected_user_ids)
                    .values_list("user_id", flat=True)
                )
                if team_id in desired_admin_team_ids:
                    remaining_admin_ids.update(selected_user_ids)
                if not remaining_admin_ids:
                    team = Team.objects.get(pk=team_id)
                    form.add_error(
                        None,
                        _("'%(team_name)s' must keep at least one team administrator.")
                        % {"team_name": team.name},
                    )
                    return invalid_manage_users_response(form)

            for user in users:
                logger.info("Updating user groups", user=user, groups=groups)
                user.groups.clear()
                user.groups.add(*groups)
                user.available_cost_groups.clear()
                user.available_cost_groups.add(*cost_groups)
                TeamMembership.objects.filter(user=user).delete()
                for team in admin_teams:
                    TeamMembership.objects.create(team=team, user=user, role="admin")
                for team in member_teams:
                    TeamMembership.objects.create(team=team, user=user, role="member")
                user.monthly_max = form.cleaned_data["monthly_max"]
                user.monthly_bonus = form.cleaned_data["monthly_bonus"]
                user.save()

            if request.headers.get("HX-Request"):
                response = HttpResponse(status=204)
                response["HX-Redirect"] = reverse("manage_users")
                return response
        else:
            return invalid_manage_users_response(form)

    context = {
        "roles": Group.objects.all(),
        "form": UserGroupForm(),
        "users_data_url": reverse("manage_users_data"),
    }
    return render(request, "manage_users.html", context)


@permission_required("otto.manage_users")
def manage_user_merge(request):
    preview = None
    merge_result = None

    if request.method == "POST":
        form = UserMergeForm(request.POST)
        action = request.POST.get("action", "preview")
        if form.is_valid():
            target = form.cleaned_data["target_user"]
            sources = list(form.cleaned_data["source_users"])
            try:
                preview = build_user_merge_preview(target, sources)
                if action == "merge":
                    if not form.cleaned_data.get("confirm_merge"):
                        form.add_error(
                            "confirm_merge",
                            _("You must confirm the merge before it can be executed."),
                        )
                    else:
                        merge_result = merge_users(target, sources, actor=request.user)
                        messages.success(
                            request,
                            _("Merged %(count)s source account(s) into %(target)s.")
                            % {
                                "count": len(sources),
                                "target": merge_result.target.upn,
                            },
                        )
                        form = UserMergeForm()
                        preview = None
            except UserMergeError as exc:
                form.add_error(None, str(exc))
    else:
        form = UserMergeForm()

    context = {
        "form": form,
        "preview": preview,
        "merge_result": merge_result,
    }
    return render(request, "manage_user_merge.html", context)


EXTERNAL_TOOL_APPROVAL_LOG_PAGE_SIZE_CHOICES = [
    ("50", "50"),
    ("100", "100"),
    ("200", "200"),
    ("400", "400"),
    ("all", _("All")),
]

EXTERNAL_TOOL_APPROVAL_LOG_SORT_OPTIONS = {
    "logged_at_desc": ("-created_at",),
    "logged_at_asc": ("created_at",),
    "user_asc": ("user_upn_sort", "-created_at"),
    "user_desc": ("-user_upn_sort", "-created_at"),
}


def _get_external_tool_approval_logs_queryset(request):
    approval_logs = ExternalToolApprovalLog.objects.select_related(
        "user", "message"
    ).annotate(
        user_upn_sort=Coalesce("user__upn", Value("")),
        pii_entity_categories_search=Cast("pii_entity_categories", models.TextField()),
    )

    search_query = (request.GET.get("q") or "").strip()
    if search_query:
        filters = (
            Q(user__upn__icontains=search_query)
            | Q(tool_name__icontains=search_query)
            | Q(tool_label__icontains=search_query)
            | Q(external_service_name__icontains=search_query)
            | Q(query__icontains=search_query)
            | Q(pii_entity_categories_search__icontains=search_query)
        )
        if search_query.isdigit():
            filters |= Q(message_id_snapshot=int(search_query))
        approval_logs = approval_logs.filter(filters)

    decision = (request.GET.get("decision") or "").strip()
    valid_decisions = {
        choice[0]
        for choice in ExternalToolApprovalLog._meta.get_field("decision").choices
    }
    if decision in valid_decisions:
        approval_logs = approval_logs.filter(decision=decision)
    else:
        decision = ""

    pii_flagged = (request.GET.get("pii_flagged") or "").strip().lower()
    if pii_flagged == "yes":
        approval_logs = approval_logs.filter(pii_flagged=True)
    elif pii_flagged == "no":
        approval_logs = approval_logs.filter(pii_flagged=False)
    else:
        pii_flagged = ""

    sort = (request.GET.get("sort") or "logged_at_desc").strip()
    if sort not in EXTERNAL_TOOL_APPROVAL_LOG_SORT_OPTIONS:
        sort = "logged_at_desc"
    approval_logs = approval_logs.order_by(
        *EXTERNAL_TOOL_APPROVAL_LOG_SORT_OPTIONS[sort]
    )

    return approval_logs, {
        "search_query": search_query,
        "selected_decision": decision,
        "selected_pii_flagged": pii_flagged,
        "selected_sort": sort,
    }


def _build_external_tool_approval_logs_csv_response(approval_logs):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        'attachment; filename="external_tool_approval_logs.csv"'
    )

    writer = csv.writer(response)
    writer.writerow(
        [
            "logged_at",
            "user",
            "message_id",
            "service",
            "tool",
            "decision",
            "source",
            "review_time_seconds",
            "pii_flagged",
            "pii_flag_source",
            "pii_entity_categories",
            "query",
        ]
    )

    for log in approval_logs.iterator():
        writer.writerow(
            [
                timezone.localtime(log.created_at).isoformat(),
                log.user.upn if log.user else "",
                log.message_reference_id or "",
                log.external_service_name or "",
                log.tool_name,
                log.get_decision_display(),
                log.approval_source_display or "",
                log.review_latency_seconds,
                "Yes" if log.pii_flagged else "No",
                str(log.pii_flag_source_display),
                log.pii_entity_categories_display,
                log.query,
            ]
        )

    return response


@permission_required("otto.manage_users")
def manage_external_tool_approvals(request):
    approval_logs, filters = _get_external_tool_approval_logs_queryset(request)
    expand_queries = (request.GET.get("expand_queries") or "").strip().lower()
    expand_all_queries = expand_queries == "all"

    if (request.GET.get("download") or "").strip().lower() == "csv":
        return _build_external_tool_approval_logs_csv_response(approval_logs)

    filtered_total = approval_logs.count()
    selected_page_size = (request.GET.get("page_size") or "50").strip().lower()
    valid_page_sizes = {
        value for value, _label in EXTERNAL_TOOL_APPROVAL_LOG_PAGE_SIZE_CHOICES
    }
    if selected_page_size not in valid_page_sizes:
        selected_page_size = "50"

    per_page = (
        max(filtered_total, 1)
        if selected_page_size == "all"
        else int(selected_page_size)
    )
    page_obj = Paginator(approval_logs, per_page).get_page(request.GET.get("page") or 1)

    pagination_query_params = request.GET.copy()
    for key in ("page", "download"):
        pagination_query_params.pop(key, None)

    sort_query_params = request.GET.copy()
    for key in ("page", "sort", "download"):
        sort_query_params.pop(key, None)

    download_query_params = pagination_query_params.copy()
    download_query_params["download"] = "csv"

    toggle_expand_query_params = pagination_query_params.copy()
    if expand_all_queries:
        toggle_expand_query_params.pop("expand_queries", None)
    else:
        toggle_expand_query_params["expand_queries"] = "all"

    download_url = reverse("manage_external_tool_approvals")
    if download_query_params:
        download_url = f"{download_url}?{download_query_params.urlencode()}"

    toggle_expand_url = reverse("manage_external_tool_approvals")
    if toggle_expand_query_params:
        toggle_expand_url = (
            f"{toggle_expand_url}?{toggle_expand_query_params.urlencode()}"
        )

    context = {
        "page_obj": page_obj,
        "decision_choices": ExternalToolApprovalLog._meta.get_field("decision").choices,
        "filtered_total": filtered_total,
        "page_size_choices": EXTERNAL_TOOL_APPROVAL_LOG_PAGE_SIZE_CHOICES,
        "selected_page_size": selected_page_size,
        "pagination_querystring": pagination_query_params.urlencode(),
        "sort_querystring": sort_query_params.urlencode(),
        "download_url": download_url,
        "toggle_expand_url": toggle_expand_url,
        "expand_all_queries": expand_all_queries,
        "logged_at_sort_next": "logged_at_asc"
        if filters["selected_sort"] == "logged_at_desc"
        else "logged_at_desc",
        "user_sort_next": "user_desc"
        if filters["selected_sort"] == "user_asc"
        else "user_asc",
        **filters,
    }
    return render(request, "manage_external_tool_approvals.html", context)


@permission_required("otto.manage_users")
def manage_users_data(request):
    try:
        draw = int(request.GET.get("draw", 0))
    except (TypeError, ValueError):
        draw = 0
    try:
        start = int(request.GET.get("start", 0))
    except (TypeError, ValueError):
        start = 0
    try:
        length = int(request.GET.get("length", 10))
    except (TypeError, ValueError):
        length = 10

    search_value = (request.GET.get("search[value]", "") or "").strip()
    entra_status_filter = (request.GET.get("entra_status", "") or "").strip().lower()
    visible_search_fields = _parse_manage_users_visible_search_fields(request)
    today = timezone.localdate()
    last_7_days_start = today - timedelta(days=6)
    last_30_days_start = today - timedelta(days=29)

    base_qs = User.objects.annotate(
        cost_7_days_annotation=Coalesce(
            Subquery(
                _user_cost_subquery(start_date=last_7_days_start),
                output_field=models.DecimalField(max_digits=12, decimal_places=6),
            ),
            Value(Decimal("0")),
            output_field=models.DecimalField(max_digits=12, decimal_places=6),
        ),
        cost_30_days_annotation=Coalesce(
            Subquery(
                _user_cost_subquery(start_date=last_30_days_start),
                output_field=models.DecimalField(max_digits=12, decimal_places=6),
            ),
            Value(Decimal("0")),
            output_field=models.DecimalField(max_digits=12, decimal_places=6),
        ),
        cost_all_time_annotation=Coalesce(
            Subquery(
                _user_cost_subquery(),
                output_field=models.DecimalField(max_digits=12, decimal_places=6),
            ),
            Value(Decimal("0")),
            output_field=models.DecimalField(max_digits=12, decimal_places=6),
        ),
        cost_7_days_text=Cast(
            "cost_7_days_annotation", output_field=models.CharField()
        ),
        cost_30_days_text=Cast(
            "cost_30_days_annotation", output_field=models.CharField()
        ),
        cost_all_time_text=Cast(
            "cost_all_time_annotation", output_field=models.CharField()
        ),
        role_names=Coalesce(
            StringAgg(
                "groups__name", delimiter="|", distinct=True, ordering="groups__name"
            ),
            models.Value("", output_field=models.TextField()),
            output_field=models.TextField(),
        ),
        cost_group_names=Coalesce(
            StringAgg(
                "available_cost_groups__name",
                delimiter="|",
                distinct=True,
                ordering="available_cost_groups__name",
            ),
            models.Value("", output_field=models.TextField()),
            output_field=models.TextField(),
        ),
        team_names=Coalesce(
            StringAgg(
                "team_memberships__team__name",
                delimiter="|",
                distinct=True,
                ordering="team_memberships__team__name",
            ),
            models.Value("", output_field=models.TextField()),
            output_field=models.TextField(),
        ),
    )

    total_count = filtered_count = User.objects.count()

    if entra_status_filter and entra_status_filter in {
        choice for choice, _label in User.EntraStatus.choices
    }:
        base_qs = base_qs.filter(entra_status=entra_status_filter)
        filtered_count = base_qs.count()

    if search_value:
        normalized_search = search_value.lower()
        search_filters = Q()
        if "upn" in visible_search_fields:
            search_filters |= (
                Q(upn__icontains=search_value)
                | Q(first_name__icontains=search_value)
                | Q(last_name__icontains=search_value)
            )
        if "entra_status" in visible_search_fields:
            search_filters |= Q(entra_status__icontains=normalized_search)
            if normalized_search == "inactive":
                search_filters |= ~Q(entra_status=User.EntraStatus.ACTIVE)
        if "job_title" in visible_search_fields:
            search_filters |= Q(job_title__icontains=search_value)
        if "preferred_language" in visible_search_fields:
            search_filters |= Q(preferred_language__icontains=search_value)
        if "roles" in visible_search_fields:
            search_filters |= Q(role_names__icontains=search_value)
        if "cost_groups" in visible_search_fields:
            search_filters |= Q(cost_group_names__icontains=search_value)
        if "teams" in visible_search_fields:
            search_filters |= Q(team_names__icontains=search_value)
        cost_search_value = search_value.replace("$", "")
        if "cost_7_days" in visible_search_fields:
            search_filters |= Q(cost_7_days_text__icontains=cost_search_value)
        if "cost_30_days" in visible_search_fields:
            search_filters |= Q(cost_30_days_text__icontains=cost_search_value)
        if "cost_all_time" in visible_search_fields:
            search_filters |= Q(cost_all_time_text__icontains=cost_search_value)
        if "last_login" in visible_search_fields:
            search_filters |= Q(last_login__icontains=search_value)

        base_qs = base_qs.filter(search_filters).distinct()
        filtered_count = base_qs.count()

    queryset = base_qs

    column_map = {
        "0": "",
        "1": "upn",
        "2": "entra_status",
        "3": "job_title",
        "4": "preferred_language",
        "5": "last_login",
        "6": "cost_7_days_annotation",
        "7": "cost_30_days_annotation",
        "8": "cost_all_time_annotation",
        "9": "role_names",
        "10": "cost_group_names",
        "11": "team_names",
        "12": "edit",
        "13": "DT_RowId",
    }

    order_column = request.GET.get("order[0][column]", "1")
    order_dir = request.GET.get("order[0][dir]", "asc")
    order_field = column_map.get(order_column, "upn")
    if order_field == "last_login":
        if order_dir == "desc":
            queryset = queryset.order_by(
                models.F("last_login").desc(nulls_last=True), "id"
            )
        else:
            queryset = queryset.order_by(
                models.F("last_login").asc(nulls_last=True), "id"
            )
    else:
        if order_dir == "desc":
            order_field = f"-{order_field}"
        queryset = queryset.order_by(order_field, "id")

    if length != -1:
        queryset = queryset[start : start + length]
    else:
        queryset = queryset[start:]

    queryset = queryset.prefetch_related(
        "groups", "available_cost_groups", "team_memberships__team"
    )

    # Build response data
    data = []
    for user in queryset:
        last_login = timezone.localtime(user.last_login) if user.last_login else ""
        cost_7_days = user.cost_7_days_annotation or Decimal("0")
        cost_30_days = user.cost_30_days_annotation or Decimal("0")
        cost_all_time = user.cost_all_time_annotation or Decimal("0")

        data.append(
            [
                "",
                user.upn,
                render_to_string("components/manage/user_status.html", {"user": user}),
                user.job_title,
                user.preferred_language,
                render_to_string(
                    "components/manage/user_last_login.html", {"last_login": last_login}
                ),
                {
                    "display": display_cad_cost(cost_7_days),
                    "sort": float(cost_7_days),
                    "filter": display_cad_cost(cost_7_days),
                },
                {
                    "display": display_cad_cost(cost_30_days),
                    "sort": float(cost_30_days),
                    "filter": display_cad_cost(cost_30_days),
                },
                {
                    "display": display_cad_cost(cost_all_time),
                    "sort": float(cost_all_time),
                    "filter": display_cad_cost(cost_all_time),
                },
                render_to_string(
                    "components/manage/user_roles.html", {"roles": user.groups.all()}
                ),
                render_to_string(
                    "components/manage/user_cost_groups.html",
                    {"cost_groups": user.available_cost_groups.all()},
                ),
                render_to_string(
                    "components/manage/user_teams.html",
                    {"team_memberships": user.team_memberships.select_related("team")},
                ),
                render_to_string(
                    "components/manage/edit_link.html",
                    {"url": reverse("manage_users_form", kwargs={"user_id": user.id})},
                ),
                str(user.id),
            ]
        )

    return JsonResponse(
        {
            "draw": draw,
            "recordsTotal": total_count,
            "recordsFiltered": filtered_count,
            "data": data,
        }
    )


@permission_required("otto.manage_users")
def manage_users_form(request, user_id=None):
    user_ids = request.GET.get("user_ids")
    if user_ids:
        user_ids = [int(_id) for _id in user_ids.split(",") if _id.isdigit()]
    elif user_id:
        user_ids = [user_id]
    if user_ids:
        logger.info("Accessing user roles form", update_user_id=user_ids)

        users = User.objects.filter(id__in=user_ids)
        form_values = {"upn": users}
        multiple_values = {
            "group": [
                ",".join(sorted(str(g) for g in user.groups.all())) for user in users
            ],
            "cost_group": [
                ",".join(sorted(str(g) for g in user.available_cost_groups.all()))
                for user in users
            ],
            "teams_admin": [
                ",".join(
                    sorted(
                        Team.objects.filter(
                            memberships__user=user, memberships__role="admin"
                        ).values_list("name", flat=True)
                    )
                )
                for user in users
            ],
            "teams_member": [
                ",".join(
                    sorted(
                        Team.objects.filter(
                            memberships__user=user, memberships__role="member"
                        ).values_list("name", flat=True)
                    )
                )
                for user in users
            ],
            "monthly_max": [user.monthly_max for user in users],
            "monthly_bonus": [user.monthly_bonus for user in users],
        }
        # Add fields to form_values only if they are the same for all users, and not an empty string
        for k, v in multiple_values.items():
            if len(set(v)) == 1 and v[0] != "":
                form_values[k] = v[0]
        if form_values.get("group"):
            # We actually need a list of Group objects; we just couldn't compare them easily
            form_values["group"] = users.first().groups.all()
        if form_values.get("cost_group"):
            form_values["cost_group"] = users.first().available_cost_groups.all()
        if form_values.get("teams_admin"):
            form_values["teams_admin"] = Team.objects.filter(
                memberships__user=users.first(), memberships__role="admin"
            )
        if form_values.get("teams_member"):
            form_values["teams_member"] = Team.objects.filter(
                memberships__user=users.first(), memberships__role="member"
            )
        form = UserGroupForm(initial=form_values)
    else:
        form = UserGroupForm()
    return render(request, "components/user_roles_modal.html", {"form": form})


@permission_required("otto.manage_users")
def modify_variables_modal(request):
    """Return modal content for editing site-wide variables stored on OttoStatus singleton."""
    otto_status = OttoStatus.objects.singleton()
    form = OttoStatusForm(instance=otto_status)
    return render(request, "components/modify_variables_modal.html", {"form": form})


@permission_required("otto.manage_users")
@require_POST
def modify_variables_update(request):
    """Handle update of OttoStatus variables from modal form (HTMX POST).
    Returns a small success fragment or re-rendered form with errors."""
    otto_status = OttoStatus.objects.singleton()
    form = OttoStatusForm(request.POST, instance=otto_status)
    if not form.is_valid():
        return render(request, "components/modify_variables_modal.html", {"form": form})

    form.save()

    messages.success(request, _("Site variables updated successfully."))

    # Return a small HTMX fragment that closes the modal
    html = """
    <script>
        (function(){
            var modalEl = document.getElementById('modifyVariablesModal');
            try {
                var m = bootstrap.Modal.getInstance(modalEl);
                if (m) m.hide();
            } catch (e) {}
        })();
    </script>
    """
    return HttpResponse(html)


@permission_required("otto.manage_users")
@require_POST
def manage_users_upload(request):
    roles = Group.objects.all()
    cost_group_lookup = {
        cost_group.cost_group_id.lower(): cost_group
        for cost_group in CostGroup.objects.all()
    }
    if request.method == "POST":
        csv_file = request.FILES.get("csv_file", None)
        if csv_file is not None:
            imported_rows = 0
            # utf-8-sig gracefully handles files saved with UTF-8 BOM (common after spreadsheet edits)
            data_set = csv_file.read().decode("utf-8-sig")
            io_string = io.StringIO(data_set)
            reader = csv.DictReader(io_string)
            for row in reader:
                try:
                    # Process your row here
                    upn = (row.get("upn") or "").strip()
                    if not upn:
                        logger.warning("Skipping upload row without upn", row=row)
                        continue
                    dot_name = upn.split("@")[0]
                    name_parts = [part for part in dot_name.split(".") if part]
                    given_name = name_parts[0] if name_parts else dot_name
                    surname = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
                    try:
                        validate_email(upn)
                        email = upn
                    except ValidationError as e:
                        email = ""
                        logger.error(f"UPN must be an email address ({upn}): {e}")
                        continue
                    # Check for monthly_max column
                    monthly_max = row.get("monthly_max", None)
                    try:
                        monthly_max = int(monthly_max)
                    except Exception:
                        monthly_max = None

                    cost_groups_value = None
                    cost_groups_column_present = False
                    for key in ["cost_groups", "cost_group_ids", "cost_group_id"]:
                        if key in row:
                            cost_groups_column_present = True
                            cost_groups_value = row.get(key)
                            break
                    resolved_cost_groups = []
                    missing_cost_groups = []
                    if cost_groups_column_present:
                        if cost_groups_value:
                            normalized = cost_groups_value.replace(",", "|")
                            slug_list = [
                                slug.strip().lower()
                                for slug in normalized.split("|")
                                if slug.strip()
                            ]
                            for slug in slug_list:
                                cost_group = cost_group_lookup.get(slug)
                                if cost_group:
                                    resolved_cost_groups.append(cost_group)
                                else:
                                    missing_cost_groups.append(slug)
                        # If column present but empty, we treat it as clearing cost groups

                    user = User.objects.find_by_upn(upn)
                    if not user:
                        user = User.objects.create_user(
                            upn=upn,
                            email=email,
                            first_name=given_name,
                            last_name=surname,
                        )
                        created = True
                    else:
                        created = False
                    if created:
                        user.email = email
                        user.first_name = given_name
                        user.last_name = surname
                        if monthly_max is not None:
                            user.monthly_max = monthly_max
                        user.save()
                    if not created:
                        user.groups.clear()
                        if monthly_max is not None:
                            user.monthly_max = monthly_max
                            user.save()
                    if cost_groups_column_present:
                        user.available_cost_groups.set(resolved_cost_groups)
                        if missing_cost_groups:
                            logger.warning(
                                "Unknown cost groups in upload",
                                upn=upn,
                                slugs=missing_cost_groups,
                            )
                    for role in (row.get("roles") or "").split("|"):
                        role = role.strip()
                        if not role:
                            continue
                        try:
                            group = roles.get(name__iexact=role)
                            user.groups.add(group)
                        except ObjectDoesNotExist:
                            pass
                    imported_rows += 1
                except Exception as e:
                    from otto.utils.common import generate_ai_error_summary

                    error_id = str(uuid.uuid4())[:7]
                    logger.error(
                        f"Error processing row {row}: {e}",
                        error_id=error_id,
                    )
                    error_msg = generate_ai_error_summary(e, error_id)
                    messages.error(request, error_msg)
            if imported_rows:
                messages.success(
                    request,
                    _("Imported %(count)s user role row(s) from CSV.")
                    % {"count": imported_rows},
                )
        else:
            logger.info("No csv file found in the submitted form.")

    # Redirect to manage_users
    return redirect("manage_users")


@permission_required("otto.manage_users")
def manage_users_download(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="otto_users.csv"'

    writer = csv.writer(response)
    writer.writerow(
        [
            "upn",
            "entra_status",
            "job_title",
            "preferred_language",
            "last_login",
            "cost_7_days",
            "cost_30_days",
            "cost_all_time",
            "roles",
            "monthly_max",
            "cost_groups",
        ]
    )

    # Include all users (including users without roles)
    today = timezone.localdate()
    last_7_days_start = today - timedelta(days=6)
    last_30_days_start = today - timedelta(days=29)

    users = (
        User.objects.annotate(
            cost_7_days_annotation=Coalesce(
                Subquery(
                    _user_cost_subquery(start_date=last_7_days_start),
                    output_field=models.DecimalField(max_digits=12, decimal_places=6),
                ),
                Value(Decimal("0")),
                output_field=models.DecimalField(max_digits=12, decimal_places=6),
            ),
            cost_30_days_annotation=Coalesce(
                Subquery(
                    _user_cost_subquery(start_date=last_30_days_start),
                    output_field=models.DecimalField(max_digits=12, decimal_places=6),
                ),
                Value(Decimal("0")),
                output_field=models.DecimalField(max_digits=12, decimal_places=6),
            ),
            cost_all_time_annotation=Coalesce(
                Subquery(
                    _user_cost_subquery(),
                    output_field=models.DecimalField(max_digits=12, decimal_places=6),
                ),
                Value(Decimal("0")),
                output_field=models.DecimalField(max_digits=12, decimal_places=6),
            ),
        )
        .distinct()
        .order_by("last_name")
    )

    for user in users:
        roles = "|".join(user.groups.values_list("name", flat=True))
        cost_groups = "|".join(
            user.available_cost_groups.order_by("cost_group_id").values_list(
                "cost_group_id", flat=True
            )
        )
        last_login = (
            timezone.localtime(user.last_login).isoformat() if user.last_login else ""
        )
        writer.writerow(
            [
                user.upn,
                user.entra_status,
                user.job_title,
                user.preferred_language,
                last_login,
                display_cad_cost(user.cost_7_days_annotation or Decimal("0")),
                display_cad_cost(user.cost_30_days_annotation or Decimal("0")),
                display_cad_cost(user.cost_all_time_annotation or Decimal("0")),
                roles,
                user.monthly_max,
                cost_groups,
            ]
        )

    return response


@permission_required("otto.manage_users")
def manage_cost_groups(request):
    if request.method == "POST":
        cost_group_id = request.POST.get("id")
        if cost_group_id:
            cost_group = get_object_or_404(CostGroup, pk=cost_group_id)
            form = CostGroupForm(request.POST, instance=cost_group)
        else:
            form = CostGroupForm(request.POST)

        if form.is_valid():
            cost_group = form.save(commit=False)
            cost_group.save()
            form.save_m2m()  # Save ManyToMany relationships (users field)
        else:
            messages.error(request, form.errors)

    context = {
        "cost_groups": CostGroup.objects.order_by("name"),
        "form": CostGroupForm(),
    }
    return render(request, "manage_cost_groups.html", context)


@permission_required("otto.manage_users")
def manage_cost_groups_form(request, cost_group_id=None):
    if cost_group_id and request.method == "DELETE":
        cost_group = get_object_or_404(CostGroup, pk=cost_group_id)
        cost_group.delete()
        response = HttpResponse()
        # Add hx-redirect header to trigger HTMX redirect
        response["HX-Redirect"] = reverse("manage_cost_groups")
        return response
    if cost_group_id:
        cost_group = get_object_or_404(CostGroup, pk=cost_group_id)
        form = CostGroupForm(instance=cost_group)
    else:
        form = CostGroupForm()
    return render(request, "components/cost_group_modal.html", {"form": form})


def aggregate_costs(costs, x_axis="day", end_date=None):
    # Aggregate the costs by the selected x-axis
    if x_axis == "feature":
        costs = costs.values("feature").annotate(total_cost=models.Sum("usd_cost"))
    elif x_axis == "cost_group":
        costs = (
            costs.annotate(
                cost_group_display=Coalesce(
                    "cost_group__name", Value(str(_("No cost group (personal costs)")))
                )
            )
            .values("cost_group_display")
            .annotate(total_cost=models.Sum("usd_cost"))
        )
        costs = [{**c, "cost_group": c.pop("cost_group_display")} for c in costs]
    elif x_axis == "user":
        costs = costs.values("user__upn").annotate(total_cost=models.Sum("usd_cost"))
        costs = [{**c, "user": c.pop("user__upn")} for c in costs]
    elif x_axis == "cost_type":
        costs = costs.values("cost_type__name").annotate(
            total_cost=models.Sum("usd_cost")
        )
        costs = [{**c, "cost_type": c.pop("cost_type__name")} for c in costs]
    else:
        # Special handling for dates
        costs = costs.values("date_incurred").annotate(
            total_cost=models.Sum("usd_cost")
        )
        costs = [{**c, "day": c.pop("date_incurred")} for c in costs]
        # Fill missing dates (if any) with zero costs, up until today's date
        if costs:
            start_date = costs[0]["day"]
        else:
            start_date = timezone.now().date()
        if not end_date:
            end_date = timezone.now().date()
        date_range = [
            start_date + timedelta(days=x)
            for x in range((end_date - start_date).days + 1)
        ]
        costs_dict = {c["day"]: c for c in costs}
        costs = [
            costs_dict.get(date, {"day": date, "total_cost": 0}) for date in date_range
        ]
        if x_axis == "week":
            costs = [
                {
                    "week": c["day"].strftime("%Y-%W"),
                    "total_cost": c["total_cost"],
                }
                for c in costs
            ]
        elif x_axis == "month":
            costs = [
                {
                    "month": c["day"].strftime("%Y-%m"),
                    "total_cost": c["total_cost"],
                }
                for c in costs
            ]
        if x_axis in ["week", "month"]:
            # Sum the costs for each week or month
            costs = [
                {
                    f"{x_axis}": week_or_month,
                    "total_cost": sum(
                        c["total_cost"] for c in costs if c[x_axis] == week_or_month
                    ),
                }
                for week_or_month in set(c[x_axis] for c in costs)
            ]
        # Sort by x-axis label
        costs = sorted(costs, key=lambda c: c[x_axis])
    return costs


@permission_required("otto.manage_users")
def list_blocked_urls(request):
    blocked_urls = BlockedURL.objects.values_list("url", flat=True).distinct()
    # Get the domains from the blocked URLs
    domains = [
        get_tld_extractor()(urlparse(url).netloc).registered_domain
        for url in blocked_urls
        if url
    ]
    domains = [domain for domain in domains if domain]
    domain_counts = Counter(domains)
    # Sort the domains by the number of blocked URLs
    domain_counts = dict(
        sorted(domain_counts.items(), key=lambda item: (-item[1], item[0]))
    )
    return render(request, "blocked_urls.html", {"domain_counts": domain_counts})


# AU-7: Aggregates and presents cost data in a dashboard
@permission_required("otto.manage_cost_dashboard")
def cost_dashboard(request):
    """
    Displays a responsive dashboard with cost data.
    X axis aggregations can be:
    feature (e.g. "chat", "qa", "summarize") - in constant FEATURE_CHOICES
    individual users (i.e. top X users) or cost groups - in models User, CostGroup
    cost type (e.g. "GPT-4 input tokens", "embedding tokens", "file translation pages") - in model CostType
    date aggregation (daily, weekly, monthly) (Cost.date_incurred) - usually primary aggregation
    """

    bar_chart_type_labels = {
        "grouped": _("Grouped"),
        "stacked": _("Stacked"),
    }

    # Options for the dropdowns
    cost_group_options = {"all": _("All cost groups")}
    cost_group_options.update({p.id: p.name for p in list(CostGroup.objects.all())})
    feature_options = {"all": _("All features")}
    feature_options.update({f[0]: f[1] for f in FEATURE_CHOICES})
    cost_type_options = {"all": _("All cost types")}
    cost_type_options.update({c.id: c.name for c in list(CostType.objects.all())})

    # Normalize GET data so form binding retains cost group selection regardless of param name
    get_data = request.GET.copy()
    # Handle autocomplete parameter name (dashboard_cost_groups) or form field name (cost_group)
    if "cost_group" not in get_data and "dashboard_cost_groups" in get_data:
        get_data["cost_group"] = get_data["dashboard_cost_groups"]
    elif "cost_group" not in get_data and "all_active_cost_groups" in get_data:
        # Map legacy autocomplete param name to form field name
        get_data["cost_group"] = get_data["all_active_cost_groups"]

    # Extract filters from normalized data
    x_axis = get_data.get("x_axis", "day")
    group = get_data.get("group", "feature")
    bar_chart_type = get_data.get("bar_chart_type", "stacked")
    cost_group = get_data.get("cost_group", "all")
    feature = get_data.get("feature", "all")
    cost_type = get_data.get("cost_type", "all")
    date_group = get_data.get("date_group", "last_30_days")
    start_date = get_data.get("start_date", None)
    end_date = get_data.get("end_date", None)

    form = CostDashboardForm(
        get_data or None,
        feature_options=feature_options,
        cost_type_options=cost_type_options,
    )
    # Ensure initial cost group selection persists in the autocomplete widget after HTMX swap
    # Handle both synthetic values and actual cost group IDs
    if not form.is_bound and cost_group not in [None, "all", "personal", "cost_groups"]:
        try:
            form.fields["cost_group"].initial = CostGroup.objects.get(pk=cost_group)
        except CostGroup.DoesNotExist:
            pass
    x_axis_labels = dict(form.fields["x_axis"].choices)
    group_labels = dict(form.fields["group"].choices)
    date_group_options = dict(form.fields["date_group"].choices)

    raw_costs = Cost.objects.all()

    # Filter by dates
    if date_group == "last_90_days":
        start_date = timezone.now().date() - timedelta(days=89)
        end_date = timezone.now().date()
    elif date_group == "last_30_days":
        start_date = timezone.now().date() - timedelta(days=29)
        end_date = timezone.now().date()
    elif date_group == "last_7_days":
        start_date = timezone.now().date() - timedelta(days=6)
        end_date = timezone.now().date()
    elif date_group == "today":
        start_date = timezone.now().date()
    elif date_group == "all":
        start_date = None
        end_date = None

    if start_date:
        raw_costs = raw_costs.filter(date_incurred__gte=start_date)
    if end_date:
        raw_costs = raw_costs.filter(date_incurred__lte=end_date)

    # If download parameter is present, download the raw_costs as a CSV file, joined with User and CostType
    if request.GET.get("download"):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="costs.csv"'

        if not start_date:
            start_date = raw_costs.aggregate(models.Min("date_incurred"))[
                "date_incurred__min"
            ]
        if not end_date:
            end_date = raw_costs.aggregate(models.Max("date_incurred"))[
                "date_incurred__max"
            ]
        raw_costs = Cost.objects.filter(
            date_incurred__gte=start_date, date_incurred__lte=end_date, usd_cost__gt=0
        ).select_related("user", "cost_type", "cost_group")

        writer = csv.writer(response)
        if not raw_costs.exists():
            writer.writerow([_("No costs found for the selected date range")])
            return response
        writer.writerow(
            [
                "date_incurred",
                "user",
                "cost_group",
                "feature",
                "cost_type",
                "usd_cost",
                "cad_cost",
            ]
        )

        feature_choices = dict(FEATURE_CHOICES)

        for cost in raw_costs:
            writer.writerow(
                [
                    cost.date_incurred,
                    cost.user.upn if cost.user else "",
                    cost.cost_group.name if cost.cost_group else "",
                    feature_choices.get(cost.feature, cost.feature),
                    cost.cost_type.name,
                    cost.usd_cost,
                    cad_cost(cost.usd_cost),
                ]
            )
        return response

    # Filter by the other filters
    # Handle synthetic cost_group filters
    if cost_group == "personal":
        # Personal costs only (no cost_group)
        raw_costs = raw_costs.filter(cost_group__isnull=True)
    elif cost_group == "cost_groups":
        # Cost group costs only (exclude personal)
        raw_costs = raw_costs.filter(cost_group__isnull=False)
    elif cost_group == "specific":
        # Filter by specific selected cost_groups
        specific_cost_groups = get_data.getlist("specific_cost_groups")
        if specific_cost_groups:
            raw_costs = raw_costs.filter(cost_group__id__in=specific_cost_groups)
        # If no specific cost_groups selected, show nothing
        else:
            raw_costs = raw_costs.none()
    elif cost_group not in [None, "all"]:
        # Specific cost_group ID (legacy support)
        raw_costs = raw_costs.filter(cost_group__id=cost_group)
    # If cost_group == "all", no filter applied (all costs)

    if feature != "all":
        raw_costs = raw_costs.filter(feature=feature)
    if cost_type != "all":
        raw_costs = raw_costs.filter(cost_type__id=cost_type)

    if x_axis in ["day", "week", "month"]:
        raw_costs = raw_costs.order_by("date_incurred")

    # Total costs, to display in the lead numbers
    total_cost_today = display_cad_cost(
        sum(c.usd_cost for c in raw_costs.filter(date_incurred=timezone.now().date()))
    )
    secondary_number_title = date_group_options.get(date_group, _("Selected dates"))
    secondary_number = display_cad_cost(sum(c.usd_cost for c in raw_costs))

    # Average cost per user per day
    if end_date and type(end_date) is str:
        end_date = timezone.datetime.strptime(end_date, "%Y-%m-%d").date()
    if start_date and type(start_date) is str:
        start_date = timezone.datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        start_date = raw_costs.aggregate(models.Min("date_incurred"))[
            "date_incurred__min"
        ]
    try:
        total_days = ((end_date or timezone.now().date()) - start_date).days + 1
    except Exception:
        total_days = 0
    total_users = raw_costs.exclude(user__isnull=True).values("user").distinct().count()
    if total_users and total_days > 0:
        tertiary_number = display_cad_cost(
            sum(c.usd_cost for c in raw_costs) / total_users / total_days
        )
        tertiary_number_title = _("Per user per day")
    else:
        tertiary_number = None
        tertiary_number_title = None

    if group == "feature":
        group_costs = [
            {"label": feature_label, "costs": raw_costs.filter(feature=feature_id)}
            for feature_id, feature_label in dict(FEATURE_CHOICES).items()
        ]
    elif group == "cost_group":
        group_costs = [
            {"label": cost_group.name, "costs": raw_costs.filter(cost_group=cost_group)}
            for cost_group in list(CostGroup.objects.all())
        ]
        # Add personal costs (no cost group)
        personal_costs = raw_costs.filter(cost_group__isnull=True)
        if personal_costs.exists():
            group_costs.append(
                {
                    "label": str(_("No cost group (personal costs)")),
                    "costs": personal_costs,
                }
            )
    elif group == "cost_type":
        group_costs = [
            {"label": cost_type.name, "costs": raw_costs.filter(cost_type=cost_type)}
            for cost_type in list(CostType.objects.all())
        ]

    costs = aggregate_costs(raw_costs, x_axis, end_date)
    chart_x_keys = [c[x_axis] for c in costs]
    # Pretty labels
    chart_x_labels = chart_x_keys
    if x_axis == "feature":
        chart_x_labels = [feature_options.get(c, c) for c in chart_x_labels]
    elif x_axis == "cost_group":
        chart_x_labels = [cost_group_options.get(c, c) for c in chart_x_labels]
    elif x_axis == "cost_type":
        chart_x_labels = [cost_type_options.get(c, c) for c in chart_x_labels]

    if group == "none":
        # Now, we have the costs aggregated by the selected x-axis
        # Let's format the data for the table
        column_headers = [x_axis_labels[x_axis], _("Total cost (CAD)")]
        rows = []
        for cost in costs:
            if x_axis == "day":
                rows.append(
                    [
                        cost["day"].strftime("%Y-%m-%d"),
                        f"${cad_cost(cost['total_cost']):.2f}",
                    ]
                )
            elif x_axis == "feature":
                rows.append(
                    [
                        feature_options.get(cost[x_axis], cost[x_axis]),
                        f"${cad_cost(cost['total_cost']):.2f}",
                    ]
                )
            else:
                rows.append([cost[x_axis], f"${cad_cost(cost['total_cost']):.2f}"])

        chart_y_groups = [
            {
                "label": _("Total cost (CAD)"),
                "values": [cad_cost(c["total_cost"]) for c in costs],
            }
        ]
    else:
        group_costs = [
            {
                "label": group_cost["label"],
                "costs": aggregate_costs(group_cost["costs"], x_axis),
            }
            for group_cost in group_costs
        ]
        # Remove group_costs which have no cost objects at all
        group_costs = [
            s for s in group_costs if sum(cost["total_cost"] for cost in s["costs"]) > 0
        ]
        # Fill in missing x-axis values with zero costs (chart_x_keys)
        for group_cost in group_costs:
            costs_dict = {c[x_axis]: c["total_cost"] for c in group_cost["costs"]}
            new_costs = [
                {
                    x_axis: x,
                    "total_cost": costs_dict.get(x, 0),
                }
                for x in chart_x_keys
            ]
            group_cost["costs"] = new_costs

        column_headers = [
            x_axis_labels[x_axis],
            group_labels[group],
            _("Total cost (CAD)"),
        ]
        rows = []
        for group_cost in group_costs:
            for cost in group_cost["costs"]:
                if cost["total_cost"] == 0:
                    continue
                if x_axis == "day":
                    rows.append(
                        [
                            cost["day"].strftime("%Y-%m-%d"),
                            group_cost["label"],
                            f"${cad_cost(cost['total_cost']):.2f}",
                        ]
                    )
                elif x_axis == "feature":
                    rows.append(
                        [
                            feature_options.get(cost[x_axis], cost[x_axis]),
                            group_cost["label"],
                            f"${cad_cost(cost['total_cost']):.2f}",
                        ]
                    )
                else:
                    rows.append(
                        [
                            cost[x_axis],
                            group_cost["label"],
                            f"${cad_cost(cost['total_cost']):.2f}",
                        ]
                    )

        chart_y_groups = sorted(
            [
                {
                    "label": group_cost["label"],
                    "values": [cad_cost(c["total_cost"]) for c in group_cost["costs"]],
                }
                for group_cost in group_costs
            ],
            key=lambda g: g["label"],
        )

    context = {
        "column_headers": column_headers,
        "rows": rows,
        "lead_number": total_cost_today,
        "lead_number_title": _("Today"),
        "secondary_number": secondary_number,
        "secondary_number_title": secondary_number_title,
        "tertiary_number": tertiary_number,
        "tertiary_number_title": tertiary_number_title,
        "chart_x_labels": chart_x_labels,
        "chart_y_groups": chart_y_groups,
        "bar_chart_type": bar_chart_type,
        "start_date": start_date,
        "end_date": end_date,
        "bar_chart_type_options": bar_chart_type_labels,
        "form": form,
    }
    return render(request, "cost_dashboard.html", context)


def usage_dashboard(request):
    """
    Displays a responsive dashboard with usage data.
    X axis aggregations can be:
    count_type (e.g. "chat_messages", "law_query", "embedding_tokens") - in constant COUNT_TYPE_CHOICES
    individual users (i.e. top X users) or cost_group groups - in models User, CostGroup
    cost type (e.g. "GPT-4 input tokens", "embedding tokens", "file translation pages") - in model CostType
    date aggregation (daily, weekly, monthly) (Cost.date_incurred) - usually primary aggregation
    """

    # Options for the dropdowns
    cost_group_options = {"all": _("All cost groups")}
    cost_group_options.update({p.id: p.name for p in list(CostGroup.objects.all())})
    chat_type_options = {"all": _("All")}
    chat_type_options.update({f[0]: f[1] for f in CHAT_TYPE_CHOICES})

    # Normalize GET data for cost_group param (legacy all_active_cost_groups -> cost_group)
    get_data = request.GET.copy()
    # Handle autocomplete parameter name (dashboard_cost_groups) or form field name (cost_group)
    if "cost_group" not in get_data and "dashboard_cost_groups" in get_data:
        get_data["cost_group"] = get_data["dashboard_cost_groups"]
    elif "cost_group" not in get_data and "all_active_cost_groups" in get_data:
        get_data["cost_group"] = get_data["all_active_cost_groups"]

    # Get the filters / groupings from the (normalized) query string
    x_axis = get_data.get("x_axis", "day")
    group = get_data.get("group", "none")
    cost_group = get_data.get("cost_group", "all")
    count_type = get_data.get("count_type", "chat_messages")
    chat_type = get_data.get("chat_type", "all")
    date_group = get_data.get("date_group", "last_30_days")
    start_date = get_data.get("start_date", None)
    end_date = get_data.get("end_date", None)

    if count_type == "embedding_tokens":
        # Override Q&A label and add documents option
        chat_type_options["qa"] = _("Q&A (queries)")
        chat_type_options.update({"librarian": _("Q&A (documents)")})
    if count_type == "files_created":
        # Files created can only be tracked for Translate and Text Extractor
        chat_type_options = {"all": _("All")}
        chat_type_options.update({"translate": _("Translate")})
        chat_type_options.update({"text_extractor": _("Text Extractor")})

    form = UsageDashboardForm(get_data or None, chat_type_options=chat_type_options)
    # Persist initial cost_group selection for autocomplete widget
    # Handle both synthetic values and actual cost_group IDs
    if not form.is_bound and cost_group not in [None, "all", "personal", "cost_groups"]:
        try:
            form.fields["cost_group"].initial = CostGroup.objects.get(pk=cost_group)
        except CostGroup.DoesNotExist:
            pass
    x_axis_labels = dict(form.fields["x_axis"].choices)
    group_labels = dict(form.fields["group"].choices)
    date_group_options = dict(form.fields["date_group"].choices)
    count_type_options = dict(form.fields["count_type"].choices)

    raw_costs = Cost.objects.all()

    # Filter by dates
    if date_group == "last_90_days":
        start_date = timezone.now().date() - timedelta(days=89)
        end_date = timezone.now().date()
    elif date_group == "last_30_days":
        start_date = timezone.now().date() - timedelta(days=29)
        end_date = timezone.now().date()
    elif date_group == "last_7_days":
        start_date = timezone.now().date() - timedelta(days=6)
        end_date = timezone.now().date()
    elif date_group == "today":
        start_date = timezone.now().date()
    elif date_group == "all":
        start_date = None
        end_date = None

    if start_date:
        raw_costs = raw_costs.filter(date_incurred__gte=start_date)
    if end_date:
        raw_costs = raw_costs.filter(date_incurred__lte=end_date)

    # Filter by the other filters
    # Handle synthetic cost_group filters
    if cost_group == "personal":
        # Personal costs only (no cost_group)
        raw_costs = raw_costs.filter(cost_group__isnull=True)
    elif cost_group == "cost_groups":
        # Cost group costs only (exclude personal)
        raw_costs = raw_costs.filter(cost_group__isnull=False)
    elif cost_group == "specific":
        # Filter by specific selected cost_groups
        specific_cost_groups = get_data.getlist("specific_cost_groups")
        if specific_cost_groups:
            raw_costs = raw_costs.filter(cost_group__id__in=specific_cost_groups)
        # If no specific cost_groups selected, show nothing
        else:
            raw_costs = raw_costs.none()
    elif cost_group not in [None, "all"]:
        # Specific cost_group ID (legacy support)
        raw_costs = raw_costs.filter(cost_group__id=cost_group)
    # If cost_group == "all", no filter applied (all costs)

    raw_costs = filter_group_count_types(raw_costs, count_type, chat_type)

    if x_axis in ["day", "week", "month"]:
        raw_costs = raw_costs.order_by("date_incurred")

    # Total counts, to display in the lead numbers
    total_count_today = calculate_aggregated_dashboard_number(
        raw_costs.filter(date_incurred=timezone.now().date()), count_type
    )
    secondary_number_title = date_group_options.get(date_group, _("Selected dates"))
    secondary_number = calculate_aggregated_dashboard_number(raw_costs, count_type)

    # Average cost per user per day
    if end_date and type(end_date) is str:
        end_date = timezone.datetime.strptime(end_date, "%Y-%m-%d").date()
    if start_date and type(start_date) is str:
        start_date = timezone.datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        start_date = raw_costs.aggregate(models.Min("date_incurred"))[
            "date_incurred__min"
        ]
    try:
        total_days = ((end_date or timezone.now().date()) - start_date).days + 1
    except Exception:
        total_days = 0
    total_users = raw_costs.exclude(user__isnull=True).values("user").distinct().count()
    if total_users and total_days > 0:
        tertiary_number = round(secondary_number / total_users / total_days, 2)
        tertiary_number_title = _("Per user per day")
    else:
        tertiary_number = None
        tertiary_number_title = None

    if group == "count_type":
        group_counts = [
            {
                "label": count_type_label,
                "counts": filter_group_count_types(raw_costs, count_type_id),
            }
            for count_type_id, count_type_label in dict(COUNT_TYPE_CHOICES).items()
        ]
    elif group == "cost_group":
        group_counts = [
            {
                "label": cost_group.name,
                "counts": raw_costs.filter(cost_group=cost_group),
            }
            for cost_group in list(CostGroup.objects.all())
        ]
        # Add personal costs (no cost group)
        personal_counts = raw_costs.filter(cost_group__isnull=True)
        if personal_counts.exists():
            group_counts.append(
                {
                    "label": str(_("No cost group (personal costs)")),
                    "counts": personal_counts,
                }
            )

    counts = aggregate_counts(raw_costs, x_axis, end_date, count_type)
    chart_x_keys = [c[x_axis] for c in counts]
    # Pretty labels
    chart_x_labels = chart_x_keys
    if x_axis == "count_type":
        chart_x_labels = [count_type_options.get(c, c) for c in chart_x_labels]
    elif x_axis == "cost_group":
        chart_x_labels = [cost_group_options.get(c, c) for c in chart_x_labels]

    if group == "none":
        # Now, we have the counts aggregated by the selected x-axis
        # Let's format the data for the table
        column_headers = [x_axis_labels[x_axis], _("Total count")]
        rows = []
        for count in counts:
            if x_axis == "day":
                rows.append(
                    [
                        count["day"].strftime("%Y-%m-%d"),
                        f"{count['total_count']:,}",
                    ]
                )
            elif x_axis == "count_type":
                rows.append(
                    [
                        count_type_options.get(count[x_axis], count[x_axis]),
                        f"{count['total_count']:,}",
                    ]
                )
            else:
                rows.append([count[x_axis], f"{count['total_count']:,}"])

        chart_y_groups = [
            {
                "label": _("Total count"),
                "values": [c["total_count"] for c in counts],
            }
        ]
    else:
        group_counts = [
            {
                "label": group_count["label"],
                "counts": aggregate_counts(
                    group_count["counts"], x_axis, count_type=count_type
                ),
            }
            for group_count in group_counts
        ]
        # Remove group_costs which have no cost objects at all
        group_counts = [
            s
            for s in group_counts
            if sum(cost["total_count"] for cost in s["counts"]) > 0
        ]
        # Fill in missing x-axis values with zero costs (chart_x_keys)
        for group_count in group_counts:
            counts_dict = {c[x_axis]: c["total_count"] for c in group_count["counts"]}
            new_counts = [
                {
                    x_axis: x,
                    "total_count": counts_dict.get(x, 0),
                }
                for x in chart_x_keys
            ]
            group_count["counts"] = new_counts

        column_headers = [
            x_axis_labels[x_axis],
            group_labels[group],
            _("Total count"),
        ]
        rows = []
        for group_count in group_counts:
            for count in group_count["counts"]:
                if count["total_count"] == 0:
                    continue
                if x_axis == "day":
                    rows.append(
                        [
                            count["day"].strftime("%Y-%m-%d"),
                            group_count["label"],
                            f"{count['total_count']}",
                        ]
                    )
                elif x_axis == "count_type":
                    rows.append(
                        [
                            count_type_options.get(count[x_axis], count[x_axis]),
                            group_count["label"],
                            f"{count['total_count']}",
                        ]
                    )
                else:
                    rows.append(
                        [
                            count[x_axis],
                            group_count["label"],
                            f"{count['total_count']}",
                        ]
                    )

        chart_y_groups = sorted(
            [
                {
                    "label": group_cost["label"],
                    "values": [c["total_count"] for c in group_cost["counts"]],
                }
                for group_cost in group_counts
            ],
            key=lambda g: g["label"],
        )

    total_count_today = f"{total_count_today:,}"
    secondary_number = f"{secondary_number:,}"
    tertiary_number = f"{tertiary_number:,}" if tertiary_number else None

    context = {
        "column_headers": column_headers,
        "rows": rows,
        "lead_number": total_count_today,
        "lead_number_title": _("Today"),
        "secondary_number": secondary_number,
        "secondary_number_title": secondary_number_title,
        "tertiary_number": tertiary_number,
        "tertiary_number_title": tertiary_number_title,
        "chart_x_labels": chart_x_labels,
        "chart_y_groups": chart_y_groups,
        "start_date": start_date,
        "end_date": end_date,
        "form": form,
    }
    return render(request, "usage_dashboard.html", context)


@cache_within_request
def _can_user_switch_cost_groups(user):
    """Cached check if user can switch cost groups"""
    from rules import has_perm

    return has_perm("otto.can_switch_cost_groups", user)


def user_cost(request):
    """
    Refreshes the user cost widget and checks for imminent session timeout
    """
    # Check if user has an active cost group selected
    active_cost_group = request.user.get_active_cost_group(request)

    if active_cost_group:
        # Show cost group costs instead of personal budget
        today_cost = cad_cost(Cost.objects.get_cost_group_cost_today(active_cost_group))
        monthly_max = active_cost_group.monthly_max
        this_month_cost = cad_cost(
            Cost.objects.get_cost_group_cost_this_month(active_cost_group)
        )
        cost_percent = max(
            min(int(100 * this_month_cost / monthly_max if monthly_max else 0), 100), 1
        )
        request_language = request.LANGUAGE_CODE
        message = (
            "{:.2f}$ / {:.2f}$ {}<br>({:.2f}$ {})"
            if request_language == "fr"
            else "${:.2f} / ${:.2f} {}<br>(${:.2f} {})"
        )
        cost_tooltip = message.format(
            this_month_cost, monthly_max, _("this month"), today_cost, _("today")
        )
        cost_tooltip_short = cost_tooltip.split("<br>")[0]

        # Check if user can switch cost groups (cached to avoid multiple queries)
        can_switch = _can_user_switch_cost_groups(request.user)

        return render(
            request,
            "components/user_cost.html",
            {
                "cost_percent": cost_percent,
                "cost_tooltip": cost_tooltip,
                "cost_tooltip_short": cost_tooltip_short,
                "cost_label": active_cost_group.name,
                "budget_type": "cost_group",
                "cost_group_name": active_cost_group.name,
                "can_switch_cost_groups": can_switch,
                "hide_cost_bar": False,
            },
        )

    # Show personal budget
    today_cost = cad_cost(Cost.objects.get_user_cost_today(request.user))
    monthly_max = request.user.this_month_max
    this_month_cost = cad_cost(Cost.objects.get_user_cost_this_month(request.user))
    cost_percent = max(
        min(int(100 * this_month_cost / monthly_max if monthly_max else 0), 100), 1
    )
    request_language = request.LANGUAGE_CODE
    message = (
        "{:.2f}$ / {:.2f}$ {}<br>({:.2f}$ {})"
        if request_language == "fr"
        else "${:.2f} / ${:.2f} {}<br>(${:.2f} {})"
    )
    cost_tooltip = message.format(
        this_month_cost, monthly_max, _("this month"), today_cost, _("today")
    )
    cost_tooltip_short = cost_tooltip.split("<br>")[0]

    # Check if user can switch cost groups (cached to avoid multiple queries)
    can_switch = _can_user_switch_cost_groups(request.user)

    # Check if the session will expire soon
    try:
        # session.get_expire_age() does not return the correct value, so we track
        # the last activity time ourselves
        last_activity_str = request.session.get("last_activity")
        time_since_last_activity = timezone.now() - timezone.datetime.fromisoformat(
            last_activity_str
        )
        time_until_expire = (
            settings.SESSION_COOKIE_AGE - time_since_last_activity.total_seconds()
        )
    except Exception as e:
        time_until_expire = 1000
        logger.info("Session expiration check failed", error=e)
    # 5 minute warning
    if time_until_expire < 60 * 5:
        from django.utils.safestring import mark_safe

        message_str = _("You will be logged out soon due to inactivity.")
        message_str += f"<br><a href='#' class='alert-link' hx-get='{reverse('extend_session')}' hx-swap='none'>"
        message_str += _("Click here to extend your session.")
        message_str += "</a>"
        messages.warning(
            request,
            mark_safe(message_str),
            extra_tags="keep-open focus unique",
        )

    return render(
        request,
        "components/user_cost.html",
        {
            "cost_percent": cost_percent,
            "cost_tooltip": cost_tooltip,
            "cost_tooltip_short": cost_tooltip_short,
            "cost_label": _("User costs"),
            "budget_type": "personal",
            "can_switch_projects": can_switch,
            "hide_cost_bar": False,
        },
    )


@permission_required("otto.can_switch_cost_groups")
def select_cost_group(request):
    if request.method == "POST":
        # Autocomplete sends data as 'user_cost_groups'
        cost_group_id = request.POST.get("user_cost_groups") or request.POST.get(
            "cost_group_id"
        )
        if cost_group_id:
            try:
                # Ensure user has access to this cost group
                available_cost_groups = CostGroup.get_available_cost_groups(
                    request.user
                )
                cost_group = available_cost_groups.get(id=cost_group_id, active=True)
                request.user.set_active_cost_group(request, cost_group)
                logger.info(
                    "Cost group selected for cost tracking",
                    cost_group_id=cost_group.id,
                    cost_group_name=cost_group.name,
                )
            except CostGroup.DoesNotExist:
                logger.warning(
                    "User attempted to select unavailable cost group",
                    cost_group_id=cost_group_id,
                )

    form = CostGroupSelectionForm()
    # Trigger cost widget update via hx-trigger header
    response = render(
        request,
        "components/cost_group_switcher.html",
        {
            "active_cost_group": request.user.get_active_cost_group(request),
            "cost_groups": CostGroup.get_available_cost_groups(request.user),
            "form": form,
        },
    )
    response["HX-Trigger"] = "update-cost-widget"
    return response


@permission_required("otto.can_switch_cost_groups")
def clear_cost_group(request):
    """Clear temporary cost group selection"""
    request.user.clear_active_cost_group(request)
    logger.info("Cost group selection cleared")

    form = CostGroupSelectionForm()
    response = render(
        request,
        "components/cost_group_switcher.html",
        {
            "active_cost_group": None,
            "cost_groups": CostGroup.get_available_cost_groups(request.user),
            "form": form,
        },
    )
    response["HX-Trigger"] = "update-cost-widget"
    return response


def extend_session(request):
    """
    Simply returns a message that message has been extended.
    Actual extension of session happens through ExtendSessionMiddleware.
    """
    messages.success(request, _("Session extended"))
    return HttpResponse(status=200)


@csrf_exempt
def load_test(request):
    # Load test endpoint: scenario-driven probes for infra/throughput.
    # Notes on fidelity:
    # - These paths intentionally skip HTMX/SSE UI flows and many permission checks.
    # - They target core subsystems (LLM, embeddings, vector search, document processing,
    #   Celery) to approximate backend load, not full user interaction fidelity.
    user_id = request.user.id if request.user.is_authenticated else None
    active_cost_group = (
        request.user.get_active_cost_group(request)
        if request.user.is_authenticated
        else None
    )
    cost_group_id = active_cost_group.id if active_cost_group else None
    bind_contextvars(
        feature="load_test",
        user_id=user_id,
        cost_group_id=cost_group_id,
    )
    start_time = timezone.now()
    if not cache.get("load_testing_enabled", False):
        return HttpResponse("Load testing is disabled", status=403)
    query_params = request.GET.dict()
    logger.info("Load test request", query_params=query_params)

    # Set mock_llm context variable deterministically for each request.
    # This avoids context leakage across reused worker execution contexts.
    from chat.llm import mock_llm_context

    mock_llm_enabled = "mock_llm" in query_params
    mock_llm_context.set(mock_llm_enabled)
    if mock_llm_enabled:
        bind_contextvars(mock_llm=True)

    if "error" in query_params:
        return HttpResponseServerError("Error requested")
    if "db_probe" in query_params:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return HttpResponse("DB probe OK")
        except Exception as exc:
            logger.exception("DB probe failed", error=str(exc))
            return HttpResponseServerError("DB probe failed")
    if "sleep" in query_params:
        time.sleep(int(query_params["sleep"]))
    if "user_library_permissions" in query_params:
        # Super heavy Django DB query, currently takes about 40s on local
        # (only if "heavy" query param is present)
        # Good for ORM + permission check overhead; not a direct UI flow.

        if "heavy" in query_params:
            users = User.objects.all()
        else:
            users = [User.objects.first()]
        for user in users:
            # Check if the user can edit the first library
            library = Library.objects.first()
            user.has_perm("librarian.edit_library", library)
        end_time = timezone.now()
        total_time = (end_time - start_time).total_seconds()
        return HttpResponse(
            f"Checked each user library permissions in {total_time:.2f} seconds"
        )
    if "query_laws" in query_params:
        try:
            llm = OttoLLM()
            # Uses the same retriever backend as laws search, but skips
            # filters, search params, and SSE answer generation.
            retriever = llm.get_retriever(
                "laws_lois__", top_k=25, vector_weight=0.8, hnsw=True
            )
            nodes = retriever.retrieve("query string")
            end_time = timezone.now()
            total_time = (end_time - start_time).total_seconds()
            llm.create_costs()
            return HttpResponse(
                f"Retrieved {len(nodes)} nodes in {total_time:.2f} seconds"
            )
        except Exception as e:
            # Table may not exist if laws haven't been loaded
            return HttpResponse(f"Error querying laws: {e}", status=200)
    if "laws_load_start_small" in query_params:
        from laws.models import JobStatus
        from laws.tasks import update_laws

        job_status = JobStatus.objects.singleton()
        if job_status.status in ["finished", "cancelled", "error", "not_started"]:
            result = update_laws.delay(
                small=True,
                full=False,
                const_only=False,
                reset=False,
                force_download=False,
                mock_embedding=True,
                debug=False,
                force_update=False,
            )
            return JsonResponse(
                {
                    "started": True,
                    "task_id": getattr(result, "id", None),
                    "job_status": job_status.status,
                }
            )

        return JsonResponse(
            {
                "started": False,
                "task_id": None,
                "job_status": job_status.status,
            }
        )
    if "laws_load_status" in query_params:
        from laws.models import JobStatus

        job_status = JobStatus.objects.singleton()
        running_statuses = [
            "started",
            "downloading",
            "resetting",
            "purging",
            "checking_existing",
            "generating_hashes",
            "loading_laws",
            "rebuilding_indexes",
            "updating_stats",
        ]
        return JsonResponse(
            {
                "ok": True,
                "job_status": job_status.status,
                "mid_run": job_status.status in running_statuses,
            }
        )
    if "text_extractor_merge_enqueue" in query_params:
        from django.core.files.base import ContentFile

        from otto.priorities import MEDIUM
        from otto.secure_models import AccessKey

        from text_extractor.models import InputFile, OutputFile, UserRequest
        from text_extractor.tasks import process_document_merge

        load_test_user = (
            request.user if request.user.is_authenticated else User.objects.first()
        )
        if not load_test_user:
            suffix = uuid.uuid4().hex[:8]
            load_test_user = User.objects.create_user(
                upn=f"load.test.{suffix}@example.com",
                oid=f"load_test_oid_{suffix}",
                email=f"load.test.{suffix}@example.com",
            )

        # Load-test helper path: bypass secure-model create permission checks so
        # anonymous/synthetic probes don't depend on a specific user's grants.
        access_key = AccessKey(user=load_test_user, bypass=True)
        user_request = UserRequest.objects.create(
            access_key=access_key,
            merged=True,
            name=f"load-test-{load_test_user.id}",
            ai_model="document_intelligence",
        )

        output_file = OutputFile.objects.create(
            access_key=access_key,
            file_name="load_test_merge",
            user_request=user_request,
            celery_task_ids=[],
        )

        this_dir = os.path.dirname(os.path.abspath(__file__))
        sample_pdf_path = os.path.join(
            this_dir, "../tests/librarian/test_files/example.pdf"
        )
        with open(sample_pdf_path, "rb") as f:
            input_file = InputFile.objects.create(
                access_key=access_key,
                file=ContentFile(f.read(), name="load_test_input.pdf"),
                original_filename="load_test_input.pdf",
                content_type="application/pdf",
                user_request=user_request,
            )

        active_cost_group = (
            load_test_user.get_active_cost_group(request)
            if request.user.is_authenticated and request.user == load_test_user
            else None
        )
        cost_group_id = str(active_cost_group.id) if active_cost_group else None

        result = process_document_merge.apply_async(
            kwargs={
                "input_file_ids": [str(input_file.id)],
                "output_file_id": str(output_file.id),
                "user_id": str(load_test_user.id),
                "cost_group_id": cost_group_id,
                "ai_model": "document_intelligence",
            },
            priority=MEDIUM,
        )
        output_file.celery_task_ids = [result.id]
        output_file.save(access_key=access_key)

        return JsonResponse(
            {
                "enqueued": True,
                "task_id": result.id,
                "output_file_id": str(output_file.id),
            }
        )
    if "text_extractor_task_status" in query_params:
        from text_extractor.tasks import process_document_merge

        task_id = query_params.get("text_extractor_task_status")
        if not task_id:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Missing text_extractor_task_status task id",
                },
                status=400,
            )

        result = process_document_merge.AsyncResult(task_id)
        is_ready = (
            result.ready()
            if hasattr(result, "ready")
            else result.status
            in [
                "SUCCESS",
                "FAILURE",
                "REVOKED",
            ]
        )
        info = result.info
        if isinstance(info, Exception):
            info = str(info)
        if info is not None and not isinstance(
            info, (dict, str, int, float, bool, list)
        ):
            info = str(info)

        return JsonResponse(
            {
                "ok": True,
                "task_id": task_id,
                "status": result.status,
                "ready": is_ready,
                "info": info,
            }
        )
    if "celery_sleep" in query_params:
        from otto.tasks import sleep_seconds

        sleep_seconds.delay(int(query_params["celery_sleep"]))
        return HttpResponse("Added task to queue")
    if "celery_priority_sleep" in query_params:
        from otto.priorities import HIGH, LOW, LOWEST, MEDIUM
        from otto.tasks import sleep_seconds

        try:
            seconds = int(query_params.get("celery_priority_sleep", 30))
        except (TypeError, ValueError):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "celery_priority_sleep must be an integer",
                },
                status=400,
            )

        queue_name = query_params.get("queue", settings.LIGHT_QUEUE).strip().lower()
        if queue_name not in [settings.LIGHT_QUEUE, settings.HEAVY_QUEUE]:
            return JsonResponse(
                {
                    "ok": False,
                    "error": f"queue must be one of: {settings.LIGHT_QUEUE}, {settings.HEAVY_QUEUE}",
                },
                status=400,
            )

        priority_map = {
            "high": HIGH,
            "medium": MEDIUM,
            "low": LOW,
            "lowest": LOWEST,
            str(HIGH): HIGH,
            str(MEDIUM): MEDIUM,
            str(LOW): LOW,
            str(LOWEST): LOWEST,
        }
        priority_raw = str(query_params.get("priority", "lowest")).strip().lower()
        if priority_raw not in priority_map:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "priority must be one of: high, medium, low, lowest, 0, 3, 6, 9",
                },
                status=400,
            )

        priority = priority_map[priority_raw]

        try:
            count = int(query_params.get("count", 1))
        except (TypeError, ValueError):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "count must be an integer",
                },
                status=400,
            )

        if count < 1 or count > 50:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "count must be between 1 and 50",
                },
                status=400,
            )

        result = None
        for _ in range(count):
            result = sleep_seconds.apply_async(
                args=[seconds],
                queue=queue_name,
                priority=priority,
            )

        assert result is not None
        return JsonResponse(
            {
                "ok": True,
                "task_id": result.id,
                "queue": queue_name,
                "priority": priority,
                "seconds": seconds,
                "count": count,
            }
        )
    if "llm_call" in query_params:
        # Synthetic LLM throughput probe (single prompt completion, no chat history,
        # no SSE streaming). Useful for raw model latency/cost only.
        if query_params.get("llm_call"):
            llm = OttoLLM(query_params["llm_call"])
        else:
            llm = OttoLLM()
        if "long_response" in query_params:
            response = llm.complete("Write a 5 paragraph essay on AI ethics.")
        else:
            response = llm.complete(
                "What is 'Hello' in French? Respond with the translated word only."
            )
        cost = llm.create_costs()
        end_time = timezone.now()
        total_time = (end_time - start_time).total_seconds()
        return HttpResponse(
            (
                f"LLM call took {total_time:.2f} seconds and cost ${cost:.4f} USD.<hr>"
                "<strong>Response:</strong><br>"
                f"<pre style='max-width: 500px;text-wrap: auto;'>{response}</pre>"
            )
        )
    if "embed_text" in query_params:
        # Synthetic embedding probe (not tied to document ingestion or vector writes).
        llm = OttoLLM()
        test_text = "This is a test text for embedding. " * (
            100 if "long_input" in query_params else 1
        )
        embedding = llm.embed_model.get_text_embedding(test_text)
        end_time = timezone.now()
        total_time = (end_time - start_time).total_seconds()
        cost = llm.create_costs()
        return HttpResponse(
            (
                f"Embedding took {total_time:.2f} seconds and cost ${cost:.6f} USD.<hr>"
                f"<strong>Mock embedding:</strong> {llm.mock_embedding}<hr>"
                "<strong>Embedding:</strong><br>"
                f"<pre style='max-width: 500px;text-wrap: auto;'>{embedding}</pre>"
            )
        )
    if "mock_document_loading" in query_params:
        # Create a test library and test data source
        # Simulates document processing with mocked embeddings; skips chat upload flow
        # and UI polling, but exercises Document.process + status transitions.
        library_suffix = uuid.uuid4().hex[:8]
        library = Library.objects.create(name=f"LoadTest Library {library_suffix}")
        data_source = DataSource.objects.create(
            name=f"LoadTest Data Source {library_suffix}", library=library
        )
        llm = OttoLLM(mock_embedding=True)
        this_dir = os.path.dirname(os.path.abspath(__file__))
        with open(
            os.path.join(this_dir, "../tests/librarian/test_files/example.pdf"), "rb"
        ) as f:
            saved_file = SavedFile.objects.create(content_type="application/pdf")
            saved_file.file.save("example.pdf", content=f)
            saved_file.generate_hash()
            document = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file,
            )
            document.process(mock_embedding=True)
            # Wait for document to finish, sleeping 1 second
            while document.status != "SUCCESS":
                time.sleep(1)
                document.refresh_from_db()
                if document.status == "ERROR":
                    return HttpResponseServerError("Document processing failed")
            end_time = timezone.now()
            total_time = (end_time - start_time).total_seconds()
            library.delete()
            return HttpResponse(
                f"Document processing (mock embedding) took {total_time:.2f} seconds."
            )
    if "celery_document_process" in query_params:
        # Enqueue-only path to stress Celery; does not wait for completion
        # or reflect UI status polling.
        library_suffix = uuid.uuid4().hex[:8]
        library = Library.objects.create(name=f"LoadTest Library {library_suffix}")
        data_source = DataSource.objects.create(
            name=f"LoadTest Data Source {library_suffix}", library=library
        )
        this_dir = os.path.dirname(os.path.abspath(__file__))
        with open(
            os.path.join(this_dir, "../tests/librarian/test_files/example.pdf"), "rb"
        ) as f:
            saved_file = SavedFile.objects.create(content_type="application/pdf")
            saved_file.file.save("example.pdf", content=f)
            saved_file.generate_hash()
            document = Document.objects.create(
                data_source=data_source,
                saved_file=saved_file,
            )
            document.process()
            end_time = timezone.now()
            total_time = (end_time - start_time).total_seconds()
            return HttpResponse(
                f"Document processing task enqueued in {total_time:.2f} seconds."
            )
    if "summarize_pdf" in query_params:
        # Closest to real user flow: exercises summarize_response streaming and
        # PDF processing, but bypasses HTMX chat_message/save_upload steps.
        from chat._views.load_test import load_test_summarize_pdf

        file_count = int(query_params.get("num_files", 1))
        pdf_filename = query_params.get("summarize_pdf") or "example.pdf"
        # if pdf_filename is an empty string, it means the query param was present without a value
        if not pdf_filename or pdf_filename.lower() == "true":
            pdf_filename = "example.pdf"

        use_default_models = "mock_llm" in query_params
        summarize_model = None if use_default_models else "gpt-4.1-nano"

        return load_test_summarize_pdf(
            request,
            file_count=file_count,
            pdf_filename=pdf_filename,
            summarize_model=summarize_model,
        )

    if "chat_stream" in query_params:
        # SSE streaming path that mirrors chat_response (closer to real chat usage).
        from chat._views.load_test import load_test_chat_stream

        message_text = query_params.get("chat_stream") or "Hello"
        if message_text.lower() == "true":
            message_text = "Hello"
        use_default_models = "mock_llm" in query_params
        chat_model = None if use_default_models else "gpt-4.1-nano"
        return load_test_chat_stream(
            request,
            message_text=message_text,
            chat_model=chat_model,
        )

    return HttpResponse(
        f"Response took {(timezone.now() - start_time).total_seconds():.2f} seconds"
    )


@permission_required("otto.enable_load_testing")
def enable_load_testing(request):
    cache.set("load_testing_enabled", True, timeout=3600)
    return render(request, "components/user_menu.html", {})


@permission_required("otto.enable_load_testing")
def disable_load_testing(request):
    cache.set("load_testing_enabled", False)
    return render(request, "components/user_menu.html", {})


@permission_required("otto.manage_users")
def reset_completion_flags(request):
    # Resets the tour and accepted_terms flags for the current user
    request.user.homepage_tour_completed = False
    request.user.ai_assistant_tour_completed = False
    request.user.laws_search_tour_completed = False
    request.user.chat_next_tour_completed = False
    request.user.accepted_terms_date = None
    request.user.save()
    return redirect("welcome")


@permission_required("otto.manage_banner")
def manage_banner(request):
    categories = [
        ("info", "Info"),
        ("success", "Success"),
        ("warning", "Warning"),
        ("danger", "Danger"),
    ]
    banner = cache.get("message_from_admins", None)
    timeout_hours = 24
    if banner and "timeout" in banner:
        timeout_hours = int(banner["timeout"]) // 3600
    if request.method == "POST":
        if request.POST.get("remove"):
            cache.delete("message_from_admins")
            banner = None
            timeout_hours = 24
        else:
            message_en = request.POST.get("message_en")
            message_fr = request.POST.get("message_fr")
            category = request.POST.get("category")
            timeout_hours = int(request.POST.get("timeout_hours", 24))
            timeout = timeout_hours * 3600
            if request.POST.get("preview"):
                message = (
                    message_fr
                    if getattr(request, "LANGUAGE_CODE", "en") == "fr"
                    else message_en
                )
                return render(
                    request,
                    "components/message_from_admins.html",
                    {
                        "message_from_admins": message,
                        "message_from_admins_category": category,
                        "preview": True,
                    },
                )
            if message_en and message_fr:
                banner = {
                    "message_en": message_en,
                    "message_fr": message_fr,
                    "category": category,
                    "timeout": timeout,
                }
                cache.set(
                    "message_from_admins",
                    banner,
                    timeout=timeout,
                )
            else:
                cache.delete("message_from_admins")
                banner = None
                timeout_hours = 24
    # Prepare banner for form
    banner_for_form = banner or {}
    banner_for_form["timeout_hours"] = timeout_hours
    # Also provide the rendered banner values so the manage page can show it
    message_to_show = None
    message_category = None
    if banner_for_form:
        message_to_show = (
            banner_for_form.get("message_fr")
            if getattr(request, "LANGUAGE_CODE", "en") == "fr"
            else banner_for_form.get("message_en")
        )
        message_category = banner_for_form.get("category")

    return render(
        request,
        "manage_banner.html",
        {
            "banner": banner_for_form,
            "categories": categories,
            "hide_breadcrumbs": True,
            "message_from_admins": message_to_show,
            "message_from_admins_category": message_category,
            "preview": True if message_to_show else False,
        },
    )


def mark_tour_completed(request, tour_name):
    # Tour properties on user object like this:
    # homepage_tour_completed = models.BooleanField(default=False)
    # ai_assistant_tour_completed = models.BooleanField(default=False)
    # laws_search_tour_completed = models.BooleanField(default=False)
    # chat_next_tour_completed = models.BooleanField(default=False)
    tour_property = f"{tour_name}_tour_completed"
    setattr(request.user, tour_property, True)
    request.user.save()
    return HttpResponse(status=200)


# ---------------------------------------------------------------------------
# Skill tag admin views
# ---------------------------------------------------------------------------


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance for fuzzy tag matching."""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[len(b)]


def _max_merge_distance(a: str, b: str) -> int:
    """Adaptive max edit distance for merge suggestions.

    Keep merges focused: short tags must be exact/near-exact, longer tags can
    tolerate small typos/casing variation.
    """
    n = min(len((a or "").strip()), len((b or "").strip()))
    if n <= 4:
        return 0
    if n <= 8:
        return 1
    return 2


def _cosine_similarity(a, b):
    """Cosine similarity between two vectors (lists of floats)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _skill_tag_embedding_text(tag: SkillTag) -> str:
    """Return canonical bilingual text used for tag embeddings."""
    return f"{tag.name_en or ''} / {tag.name_fr or ''}".strip(" /")


def _embed_skill_tags(tags: list[SkillTag]) -> None:
    """Compute embeddings for the provided skill tags in-place.

    Failures are logged but do not block admin actions.
    """
    tags_to_embed = [tag for tag in tags if tag and not tag.embedding]
    if not tags_to_embed:
        return

    try:
        llm = OttoLLM(mock_embedding=False)
        texts = [_skill_tag_embedding_text(tag) for tag in tags_to_embed]
        embeddings = llm.embed_model.get_text_embedding_batch(texts)
    except Exception:
        logger.exception("Failed to compute skill tag embeddings")
        return

    for tag, embedding in zip(tags_to_embed, embeddings):
        tag.embedding = embedding
    SkillTag.objects.bulk_update(tags_to_embed, ["embedding"])


@permission_required("otto.manage_users")
def manage_skill_tags(request):
    tags = SkillTag.objects.annotate(usage_count=models.Count("skills")).order_by(
        "-usage_count", "name"
    )

    # Build suggested merge pairs using both edit-distance and embedding distance.
    # Keep the embedding threshold conservative to avoid noisy semantic merges.
    max_embedding_distance = 0.35

    tag_list = list(tags)
    _embed_skill_tags(tag_list)

    suggestions = []
    seen = set()
    for i, t1 in enumerate(tag_list):
        for t2 in tag_list[i + 1 :]:
            pair_key = (min(t1.pk, t2.pk), max(t1.pk, t2.pk))
            if pair_key in seen:
                continue

            dist_en = _edit_distance(
                (t1.name_en or "").lower(), (t2.name_en or "").lower()
            )
            dist_fr = _edit_distance(
                (t1.name_fr or "").lower(), (t2.name_fr or "").lower()
            )

            en_close = dist_en <= _max_merge_distance(
                t1.name_en or "", t2.name_en or ""
            )
            fr_close = dist_fr <= _max_merge_distance(
                t1.name_fr or "", t2.name_fr or ""
            )

            best_edit_dist = min(dist_en, dist_fr)
            edit_match = en_close or fr_close

            embedding_distance = None
            if t1.embedding and t2.embedding:
                similarity = _cosine_similarity(t1.embedding, t2.embedding)
                embedding_distance = max(0.0, 1.0 - similarity)
            embedding_match = (
                embedding_distance is not None
                and embedding_distance <= max_embedding_distance
            )

            if edit_match or embedding_match:
                # Prefer pairs supported by both signals, then by tighter distances.
                sort_bucket = 0 if (edit_match and embedding_match) else 1
                suggestions.append(
                    {
                        "t1": t1,
                        "t2": t2,
                        "edit_distance": best_edit_dist,
                        "embedding_distance": embedding_distance,
                        "edit_match": edit_match,
                        "embedding_match": embedding_match,
                        "sort_bucket": sort_bucket,
                    }
                )
                seen.add(pair_key)
    suggestions.sort(
        key=lambda s: (
            s["sort_bucket"],
            s["edit_distance"],
            s["embedding_distance"]
            if s["embedding_distance"] is not None
            else float("inf"),
            -((s["t1"].usage_count or 0) + (s["t2"].usage_count or 0)),
        )
    )

    context = {
        "tags": tags,
        "suggestions": suggestions,
    }
    return render(request, "manage_skill_tags.html", context)


@permission_required("otto.manage_users")
@require_POST
def add_skill_tag(request):
    name_en = request.POST.get("name_en", "").strip()
    name_fr = request.POST.get("name_fr", "").strip()

    if not name_en or not name_fr:
        messages.error(request, _("Both English and French tag names are required."))
        return redirect("manage_skill_tags")

    existing = SkillTag.objects.filter(
        Q(name_en__iexact=name_en)
        | Q(name_fr__iexact=name_fr)
        | Q(name_en__iexact=name_fr)
        | Q(name_fr__iexact=name_en)
    ).first()
    if existing:
        messages.warning(
            request,
            _('A similar tag already exists: "%(name_en)s / %(name_fr)s".')
            % {"name_en": existing.name_en, "name_fr": existing.name_fr},
        )
        return redirect("manage_skill_tags")

    tag = SkillTag.objects.create(name=name_en, name_en=name_en, name_fr=name_fr)
    _embed_skill_tags([tag])
    messages.success(request, _("Tag added."))
    return redirect("manage_skill_tags")


@permission_required("otto.manage_users")
@require_POST
def edit_skill_tag(request, tag_id):
    tag = get_object_or_404(SkillTag, pk=tag_id)
    name_en = request.POST.get("name_en", "").strip()
    name_fr = request.POST.get("name_fr", "").strip()
    embedding_needs_refresh = False
    if name_en:
        if name_en != tag.name_en:
            embedding_needs_refresh = True
        tag.name_en = name_en
    if name_fr:
        if name_fr != tag.name_fr:
            embedding_needs_refresh = True
        tag.name_fr = name_fr
    if embedding_needs_refresh:
        tag.embedding = None
    tag.save()
    if embedding_needs_refresh or not tag.embedding:
        _embed_skill_tags([tag])
    return redirect("manage_skill_tags")


@permission_required("otto.manage_users")
@require_POST
def merge_skill_tags(request):
    source_id = request.POST.get("source_id")
    target_id = request.POST.get("target_id")
    if not source_id or not target_id or source_id == target_id:
        messages.error(request, _("Invalid merge parameters."))
        return redirect("manage_skill_tags")
    source = get_object_or_404(SkillTag, pk=source_id)
    target = get_object_or_404(SkillTag, pk=target_id)
    # Move all skill associations from source to target
    count = 0
    for skill in source.skills.all():
        if not skill.skill_tags.filter(pk=target.pk).exists():
            skill.skill_tags.add(target)
        skill.skill_tags.remove(source)
        count += 1
    source.delete()
    messages.success(
        request,
        _('Merged "%(source)s" into "%(target)s" (%(count)d skills updated).')
        % {"source": str(source), "target": str(target), "count": count},
    )
    return redirect("manage_skill_tags")


@permission_required("otto.manage_users")
@require_POST
def delete_skill_tag(request, tag_id):
    tag = get_object_or_404(SkillTag, pk=tag_id)
    usage = tag.skills.count()
    if usage > 0 and not request.POST.get("confirm"):
        messages.warning(
            request,
            _(
                'Tag "%(tag)s" is used by %(count)d skill(s). Submit again with confirmation to delete.'
            )
            % {"tag": str(tag), "count": usage},
        )
        return redirect("manage_skill_tags")
    tag.delete()
    messages.success(request, _("Tag deleted."))
    return redirect("manage_skill_tags")


# --- Team management views ---


@permission_required("otto.access_otto")
def manage_teams(request):
    """List teams the user belongs to (admins see all teams)."""
    if request.user.is_admin:
        teams = Team.objects.all()
    else:
        teams = Team.objects.filter(memberships__user=request.user).distinct()
    teams = teams.prefetch_related("memberships__user")
    team_data = []
    for team in teams:
        memberships = list(team.memberships.all())
        admin_memberships = [m for m in memberships if m.role == "admin"]
        member_memberships = [m for m in memberships if m.role == "member"]
        user_membership = next(
            (m for m in memberships if m.user_id == request.user.id),
            None,
        )
        role_badges = []
        if user_membership and user_membership.role == "admin":
            role_badges.append({"label": _("Team admin"), "class": "bg-primary"})
        elif user_membership:
            role_badges.append({"label": _("Member"), "class": "bg-secondary"})
        else:
            role_badges.append(
                {
                    "label": _("Not a member"),
                    "class": "bg-light text-dark border",
                }
            )
        if request.user.is_admin:
            role_badges.append({"label": _("Otto admin"), "class": "bg-danger"})
        can_manage_team = request.user.is_admin or any(
            m.user_id == request.user.id and m.role == "admin" for m in memberships
        )
        team_data.append(
            {
                "id": team.id,
                "name": team.name,
                "member_count": len(memberships),
                "is_team_admin": can_manage_team,
                "role_badges": role_badges,
                "admins": [m.user.email for m in admin_memberships],
                "members": [m.user.email for m in member_memberships],
            }
        )
    return render(request, "manage_teams.html", {"teams": team_data})


@permission_required("otto.access_otto")
def manage_teams_form(request, team_id=None):
    """HTMX modal form for creating/editing a team."""
    team = None
    if team_id:
        team = get_object_or_404(Team, pk=team_id)
        if not request.user.has_perm("otto.manage_team", team):
            messages.error(request, _("You don't have permission to edit this team."))
            return redirect("manage_teams")

    if request.method == "POST":
        form = TeamForm(request.POST, team=team)
        if form.is_valid():
            form.save(user=request.user)
            if team:
                messages.success(request, _("Team updated."))
            else:
                messages.success(request, _("Team created."))
            if request.headers.get("HX-Request"):
                response = HttpResponse(status=204)
                response["HX-Redirect"] = reverse("manage_teams")
                return response
            return redirect("manage_teams")
    else:
        form = TeamForm(team=team)

    return render(
        request,
        "components/team_form_modal.html",
        {"form": form, "team": team},
    )


@permission_required("otto.access_otto")
@require_POST
def delete_team(request, team_id):
    """Delete a team."""
    team = get_object_or_404(Team, pk=team_id)
    if not request.user.has_perm("otto.manage_team", team):
        messages.error(request, _("You don't have permission to delete this team."))
        return robust_redirect(request, reverse("manage_teams"))
    team.delete()
    messages.success(request, _("Team deleted."))
    return robust_redirect(request, reverse("manage_teams"))
