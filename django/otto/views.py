import csv
import io
import os
import time
from collections import defaultdict
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import validate_email
from django.db import models
from django.http import HttpRequest, HttpResponse, HttpResponseServerError, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import check_for_language
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

import tldextract
from azure_auth.views import azure_auth_login as azure_auth_login
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from chat.llm import OttoLLM
from librarian.models import DataSource, Document, Library, SavedFile
from otto.forms import (
    FeedbackForm,
    FeedbackMetadataForm,
    FeedbackNoteForm,
    PilotForm,
    UserGroupForm,
)
from otto.models import (
    FEATURE_CHOICES,
    BlockedURL,
    Cost,
    CostType,
    Feature,
    Feedback,
    Pilot,
)
from otto.utils.common import cad_cost, display_cad_cost
from otto.utils.decorators import permission_required

logger = get_logger(__name__)

User = get_user_model()


def health_check(request):
    return JsonResponse({"status": "ok"})


def welcome(request):
    # Bilingual landing page with login button
    return render(request, "welcome.html", {"next_url": request.GET.get("next", "/")})


def login(request: HttpRequest):
    # Wraps azure_auth login to allow for language selection
    lang_code = request.GET.get("lang")
    response = azure_auth_login(request)
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


def get_categorized_features(user):
    categories = defaultdict(list)
    category_choices = dict(Feature.CATEGORY_CHOICES)

    # Fetch all features along with their related apps
    features = Feature.objects.select_related("app").order_by("id")

    # Loop over the features and add them to the appropriate category
    for feature in features:
        if user.has_perm("otto.view_app", feature.app):
            categories[feature.category].append(feature)

    # Now create the new list of dicts with updated category titles
    categorized_features = [
        {
            "category_id": category,
            "category_title": category_choices.get(category, category.capitalize()),
            "features": features,
        }
        for category, features in categories.items()
    ]
    # Sort the categories by title to push Reporting to the bottom as a temporary measure while under development
    return sorted(categorized_features, key=lambda x: x["category_title"])


def index(request):
    return render(
        request,
        "index.html",
        {
            "hide_breadcrumbs": True,
            "categorized_features": get_categorized_features(request.user),
        },
    )


def topnav_search_inner(request):
    return render(
        request,
        "components/search_inner.html",
        {"categorized_features": get_categorized_features(request.user)},
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
            "redirect_url": redirect_url,
        },
    )


def feedback_message(request: HttpRequest, message_id=None):
    if message_id == "None":
        message_id = None
    if request.method == "POST":
        from django.contrib import messages

        from otto.utils.common import get_app_from_path

        form = FeedbackForm(request.user, message_id, request.POST)

        if form.is_valid():
            feedback_saved = form.save(commit=False)
            date_and_time = timezone.now().strftime("%Y%m%d-%H%M%S")
            feedback_saved.created_at = date_and_time
            if feedback_saved.chat_message is None:
                feedback_saved.app = get_app_from_path(feedback_saved.url_context)
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
        form = FeedbackForm(request.user, message_id)
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


@permission_required("otto.manage_users")
def feedback_dashboard(request, page_number=None):
    if page_number is None:
        page_number = 1

    apps = Feedback.objects.values_list("app", flat=True).distinct()
    feedback_status_choices = Feedback.FEEDBACK_STATUS_CHOICES
    feedback_type_choices = Feedback.FEEDBACK_TYPE_CHOICES

    context = {
        "apps": apps,
        "feedback_status_choices": feedback_status_choices,
        "feedback_type_choices": feedback_type_choices,
        "current_page_number": page_number,
    }

    return render(request, "feedback_dashboard.html", context)


@permission_required("otto.manage_users")
def feedback_stats(request):
    stats = Feedback.objects.get_feedback_stats()
    return render(
        request, "components/feedback/dashboard/feedback_stats.html", {"stats": stats}
    )


@permission_required("otto.manage_users")
def feedback_list(request, page_number=None):
    from django.core.paginator import Paginator

    feedback_messages = Feedback.objects.all().order_by("-created_at")

    if request.method == "POST":
        feedback_type = request.POST.get("feedback_type")
        status = request.POST.get("status")
        app = request.POST.get("app")

        if feedback_type and feedback_type != "all":
            feedback_messages = feedback_messages.filter(feedback_type=feedback_type)
        if status and status != "all":
            feedback_messages = feedback_messages.filter(status=status)
        if app and app != "all":
            feedback_messages = feedback_messages.filter(app=app)

    # Get 10 feedback messages per page
    paginator = Paginator(feedback_messages, 10)
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
    }
    return render(request, "components/feedback/dashboard/feedback_list.html", context)


@permission_required("otto.manage_users")
def feedback_dashboard_update(request, feedback_id, form_type):
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
            return HttpResponse(status=200)
        else:
            messages.error(request, form.errors)
    else:
        return HttpResponse(status=405)


@permission_required("otto.manage_users")
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
    for feedback in Feedback.objects.all().order_by("-created_at"):
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
    if request.method == "POST":
        form = UserGroupForm(request.POST)
        if form.is_valid():
            # The form contains users (multiple choice, named "upn" but value is "id")
            # and groups (multiple choice, named "group" but value is "id")
            # We want to add the selected groups to the selected users
            users = form.cleaned_data["upn"]
            groups = form.cleaned_data["group"]
            for user in users:
                logger.info("Updating user groups", user=user, groups=groups)
                user.groups.clear()
                user.groups.add(*groups)
                if "pilot" in form.cleaned_data:
                    user.pilot = form.cleaned_data["pilot"]
                user.monthly_max = form.cleaned_data["monthly_max"]
                user.monthly_bonus = form.cleaned_data["monthly_bonus"]
                user.save()
        else:
            raise ValueError(form.errors)

    context = {
        # Show the users who have already at least 1 group
        "users": User.objects.filter(groups__isnull=False)
        .distinct()
        .order_by("last_name", "first_name"),
        "roles": Group.objects.all(),
        "form": UserGroupForm(),
    }
    return render(request, "manage_users.html", context)


@permission_required("otto.manage_users")
def manage_users_form(request, user_id=None):
    if user_id:
        logger.info("Accessing user roles form", update_user_id=user_id)
        user = User.objects.get(id=user_id)
        form = UserGroupForm(
            initial={
                "upn": [user],
                "group": user.groups.all(),
                "pilot": user.pilot,
                "monthly_max": user.monthly_max,
                "monthly_bonus": user.monthly_bonus,
            }
        )
    else:
        form = UserGroupForm()
    return render(request, "components/user_roles_modal.html", {"form": form})


@permission_required("otto.manage_users")
@require_POST
def manage_users_upload(request):
    roles = Group.objects.all()
    if request.method == "POST":
        csv_file = request.FILES.get("csv_file", None)
        if csv_file is not None:
            data_set = csv_file.read().decode("UTF-8")
            io_string = io.StringIO(data_set)
            reader = csv.DictReader(io_string)
            for row in reader:
                try:
                    # Process your row here
                    upn = row["upn"]
                    dot_name = upn.split("@")[0]
                    given_name = dot_name.split(".")[0]
                    surname = dot_name.split(".")[1]
                    try:
                        validate_email(upn)
                        email = upn
                    except ValidationError as e:
                        email = ""
                        logger.error(f"UPN must be an email address ({upn}): {e}")
                        continue
                    # Get or create the pilot
                    pilot_id = row.get("pilot_id", None)
                    if pilot_id:
                        try:
                            pilot = Pilot.objects.get(pilot_id=pilot_id)
                        except ObjectDoesNotExist:
                            # Create a new pilot
                            pilot = Pilot.objects.create(
                                pilot_id=pilot_id,
                                name=pilot_id.replace("_", " ").capitalize(),
                            )
                    # Check for monthly_max column
                    monthly_max = row.get("monthly_max", None)
                    try:
                        monthly_max = int(monthly_max)
                    except Exception as e:
                        monthly_max = None
                    user = User.objects.filter(upn__iexact=upn).first()
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
                        if pilot_id:
                            user.pilot = pilot
                        if monthly_max:
                            user.monthly_max = monthly_max
                        user.save()
                    if not created:
                        user.groups.clear()
                        if pilot_id:
                            user.pilot = pilot
                        if monthly_max:
                            user.monthly_max = monthly_max
                        if pilot_id or monthly_max:
                            user.save()
                    for role in row["roles"].split("|"):
                        role = role.strip()
                        try:
                            group = roles.get(name__iexact=role)
                            user.groups.add(group)
                        except ObjectDoesNotExist:
                            pass
                except Exception as e:
                    logger.error(f"Error processing row {row}: {e}")

        else:
            logger.info("No csv file found in the submitted form.")

    # Redirect to manage_users
    return redirect("manage_users")


@permission_required("otto.manage_users")
def manage_users_download(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="otto_users.csv"'

    writer = csv.writer(response)
    writer.writerow(["upn", "pilot_id", "roles", "monthly_max"])

    # Only get users who have roles
    for user in User.objects.filter(groups__isnull=False).order_by("last_name"):
        roles = "|".join(user.groups.values_list("name", flat=True))
        pilot_id = user.pilot.pilot_id if user.pilot else ""
        writer.writerow([user.upn, pilot_id, roles, user.monthly_max])

    return response


@permission_required("otto.manage_users")
def manage_pilots(request):
    if request.method == "POST":
        pilot_id = request.POST.get("id")
        if pilot_id:
            pilot = get_object_or_404(Pilot, pk=pilot_id)
            form = PilotForm(request.POST, instance=pilot)
        else:
            form = PilotForm(request.POST)

        if form.is_valid():
            form.save()
        else:
            messages.error(request, form.errors)

    context = {
        "pilots": Pilot.objects.order_by("name"),
        "form": PilotForm(),
    }
    return render(request, "manage_pilots.html", context)


@permission_required("otto.manage_users")
def manage_pilots_form(request, pilot_id=None):
    if pilot_id and request.method == "DELETE":
        pilot = get_object_or_404(Pilot, pk=pilot_id)
        pilot.delete()
        response = HttpResponse()
        # Add hx-redirect header to trigger HTMX redirect
        response["HX-Redirect"] = reverse("manage_pilots")
        return response
    if pilot_id:
        pilot = get_object_or_404(Pilot, pk=pilot_id)
        form = PilotForm(instance=pilot)
    else:
        form = PilotForm()
    return render(request, "components/pilot_modal.html", {"form": form})


def aggregate_costs(costs, x_axis="day", end_date=None):
    # Aggregate the costs by the selected x-axis
    if x_axis == "feature":
        costs = costs.values("feature").annotate(total_cost=models.Sum("usd_cost"))
    elif x_axis == "pilot":
        costs = costs.values("user__pilot__name").annotate(
            total_cost=models.Sum("usd_cost")
        )
        costs = [{**c, "pilot": c.pop("user__pilot__name")} for c in costs]
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
    blocked_urls = BlockedURL.objects.all().values("url")
    # Get the domains from the blocked URLs
    domains = [
        tldextract.extract(urlparse(url["url"]).netloc).registered_domain
        for url in blocked_urls
    ]
    domain_counts = {domain: domains.count(domain) for domain in set(domains)}
    # Sort the domains by the number of blocked URLs
    domain_counts = dict(
        sorted(domain_counts.items(), key=lambda item: item[1], reverse=True)
    )
    return render(request, "blocked_urls.html", {"domain_counts": domain_counts})


# AU-7: Aggregates and presents cost data in a dashboard
@permission_required("otto.manage_users")
def cost_dashboard(request):
    """
    Displays a responsive dashboard with cost data.
    X axis aggregations can be:
    feature (e.g. "chat", "qa", "summarize") - in constant FEATURE_CHOICES
    individual users (i.e. top X users) or pilot groups - in models User, Pilot
    cost type (e.g. "GPT-4 input tokens", "embedding tokens", "file translation pages") - in model CostType
    date aggregation (daily, weekly, monthly) (Cost.date_incurred) - usually primary aggregation
    """

    x_axis_labels = {
        "day": _("Day"),
        "week": _("Week"),
        "month": _("Month"),
        "feature": _("Feature"),
        "pilot": _("Pilot"),
        "user": _("User"),
        "cost_type": _("Cost type"),
    }

    group_labels = {
        "none": _("None"),
        "feature": _("Feature"),
        "pilot": _("Pilot"),
        "cost_type": _("Cost type"),
    }

    bar_chart_type_labels = {
        "grouped": _("Grouped"),
        "stacked": _("Stacked"),
    }

    # Options for the dropdowns
    pilot_options = {"all": _("All pilots")}
    pilot_options.update({p.id: p.name for p in list(Pilot.objects.all())})
    feature_options = {"all": _("All features")}
    feature_options.update({f[0]: f[1] for f in FEATURE_CHOICES})
    cost_type_options = {"all": _("All cost types")}
    cost_type_options.update({c.id: c.name for c in list(CostType.objects.all())})
    date_group_options = {
        "all": _("All time"),
        "last_90_days": _("Last 90 days"),
        "last_30_days": _("Last 30 days"),
        "last_7_days": _("Last 7 days"),
        "today": _("Today"),
        "custom": _("Custom date range"),
    }

    # Get the filters / groupings from the query string
    x_axis = request.GET.get("x_axis", "day")
    group = request.GET.get("group", "feature")
    bar_chart_type = request.GET.get("bar_chart_type", "stacked")
    pilot = request.GET.get("pilot", "all")
    feature = request.GET.get("feature", "all")
    cost_type = request.GET.get("cost_type", "all")
    date_group = request.GET.get("date_group", "last_30_days")
    start_date = request.GET.get("start_date", None)
    end_date = request.GET.get("end_date", None)

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
        ).prefetch_related("user", "cost_type", "user__pilot")

        writer = csv.writer(response)
        if not raw_costs.exists():
            writer.writerow([_("No costs found for the selected date range")])
            return response
        writer.writerow(
            [
                "date_incurred",
                "user",
                "pilot",
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
                    cost.user.pilot.name if cost.user and cost.user.pilot else "",
                    feature_choices.get(cost.feature, cost.feature),
                    cost.cost_type.name,
                    cost.usd_cost,
                    cad_cost(cost.usd_cost),
                ]
            )
        return response

    # Filter by the other filters
    if pilot != "all":
        raw_costs = raw_costs.filter(user__pilot__id=pilot)
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
    if end_date and type(end_date) == str:
        end_date = timezone.datetime.strptime(end_date, "%Y-%m-%d").date()
    if start_date and type(start_date) == str:
        start_date = timezone.datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        start_date = raw_costs.aggregate(models.Min("date_incurred"))[
            "date_incurred__min"
        ]
    try:
        total_days = ((end_date or timezone.now().date()) - start_date).days + 1
    except:
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
    elif group == "pilot":
        group_costs = [
            {"label": pilot.name, "costs": raw_costs.filter(user__pilot=pilot)}
            for pilot in list(Pilot.objects.all())
        ]
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
    elif x_axis == "pilot":
        chart_x_labels = [pilot_options.get(c, c) for c in chart_x_labels]
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
        "x_axis": x_axis,
        "group": group,
        "bar_chart_type": bar_chart_type,
        "pilot": pilot,
        "feature": feature,
        "cost_type": cost_type,
        "date_group": date_group,
        "start_date": start_date,
        "end_date": end_date,
        "x_axis_options": x_axis_labels,
        "group_options": group_labels,
        "bar_chart_type_options": bar_chart_type_labels,
        "pilot_options": pilot_options,
        "feature_options": feature_options,
        "cost_type_options": cost_type_options,
        "date_group_options": date_group_options,
    }
    return render(request, "cost_dashboard.html", context)


def user_cost(request):
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
    return render(
        request,
        "components/user_cost.html",
        {
            "cost_percent": cost_percent,
            "cost_tooltip": cost_tooltip,
            "cost_tooltip_short": cost_tooltip_short,
            "cost_label": _("User costs"),
        },
    )


@csrf_exempt
def load_test(request):
    bind_contextvars(feature="load_test")
    start_time = timezone.now()
    if not cache.get("load_testing_enabled", False):
        return HttpResponse("Load testing is disabled", status=403)
    query_params = request.GET.dict()
    logger.info("Load test request", query_params=query_params)
    if "error" in query_params:
        return HttpResponseServerError("Error requested")
    if "sleep" in query_params:
        time.sleep(int(query_params["sleep"]))
    if "user_library_permissions" in query_params:
        # Super heavy Django DB query, currently takes about 40s on local
        # (only if "heavy" query param is present)

        if "heavy" in query_params:
            users = User.objects.all()
        else:
            users = [User.objects.first()]
        for user in users:
            # Check if the user can edit the first library
            library = Library.objects.first()
            user_can_edit = user.has_perm("librarian.edit_library", library)
        end_time = timezone.now()
        total_time = (end_time - start_time).total_seconds()
        return HttpResponse(
            f"Checked each user library permissions in {total_time:.2f} seconds"
        )
    if "query_laws" in query_params:
        llm = OttoLLM()
        retriever = llm.get_retriever("laws_lois__")
        nodes = retriever.retrieve("query string")
        end_time = timezone.now()
        total_time = (end_time - start_time).total_seconds()
        llm.create_costs()
        return HttpResponse(f"Retrieved {len(nodes)} nodes in {total_time:.2f} seconds")
    if "celery_sleep" in query_params:
        from otto.tasks import sleep_seconds

        sleep_seconds.delay(int(query_params["celery_sleep"]))
        return HttpResponse("Added task to queue")
    if "llm_call" in query_params:
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
                "<strong>Embedding:</strong><br>"
                f"<pre style='max-width: 500px;text-wrap: auto;'>{embedding}</pre>"
            )
        )
    if "mock_document_loading" in query_params:
        # Create a test library and test data source
        library = Library.objects.create(name="Test Library")
        data_source = DataSource.objects.create(
            name="Test Data Source", library=library
        )
        llm = OttoLLM(mock_embedding=True)
        this_dir = os.path.dirname(os.path.abspath(__file__))
        with open(
            os.path.join(this_dir, "../tests/librarian/test_files/example.pdf"), "rb"
        ) as f:
            saved_file = SavedFile.objects.create(content_type="application/pdf")
            saved_file.file.save("example.pdf", content=f)
            saved_file.generate_hash()
            document = Document.objects.create(data_source=data_source, file=saved_file)
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
