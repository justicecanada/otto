import csv
import io
from collections import defaultdict
from datetime import date, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import validate_email
from django.db import models
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import check_for_language, get_language
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from azure_auth.views import azure_auth_login as azure_auth_login
from structlog import get_logger

from chat.models import Message
from otto.forms import FeedbackForm, PilotForm, UserGroupForm
from otto.metrics.activity_metrics import otto_access_total
from otto.metrics.feedback_metrics import otto_feedback_submitted_with_comment_total
from otto.models import FEATURE_CHOICES, App, Cost, CostType, Feature, Pilot, UsageTerm
from otto.utils.common import cad_cost, display_cad_cost
from otto.utils.decorators import permission_required

logger = get_logger(__name__)

User = get_user_model()


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

    return categorized_features


def index(request):
    otto_access_total.labels(user=request.user.upn).inc()
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


@csrf_protect
def accept_terms(request):

    if request.method == "POST":
        logger.info("Terms of conditions were accepted")
        request.user.accepted_terms_date = timezone.now()
        request.user.save()

        redirect_url = request.POST.get("redirect_url") or "/"
        return redirect(redirect_url)

    redirect_url = request.GET.get("next", "/")
    usage_terms = UsageTerm.objects.all()

    return render(
        request,
        "accept_terms.html",
        {
            "hide_breadcrumbs": True,
            "redirect_url": redirect_url,
            "usage_terms": usage_terms,
        },
    )


@csrf_protect
@login_required
def message_feedback(request: HttpRequest, message_id=None):
    if request.method == "POST":
        form = FeedbackForm(request.user, message_id, request.POST)

        if form.is_valid():
            date_and_time = timezone.now().strftime("%Y%m%d-%H%M%S")
            form.cleaned_data["created_at"] = date_and_time
            form.save()

            otto_feedback_submitted_with_comment_total.labels(
                user=request.user.username
            ).inc()

            if message_id is None:
                return redirect("feedback_success")
            else:
                return HttpResponse()
    else:
        form = FeedbackForm(request.user, message_id)

    return render(
        request,
        "feedback.html",
        {
            "form": form,
            "message_id": message_id,
            "hide_breadcrumbs": True,
            "hide_nav": message_id is not None,
        },
    )


@login_required
def feedback_success(request):
    return render(request, "feedback_success.html")


@login_required
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


@login_required
def notifications(request, hide=False):
    """
    Updates the notifications badge and list of notifications
    e.g. on page load, after notification icon clicked, during polling, etc.
    """
    return render(
        request,
        "components/notifications_update.html",
        {
            "notifications": request.user.notifications.all().order_by("-created_at"),
            # Expand the notifications dropdown if there are any errors
            "show_notifications": request.user.notifications.filter(
                category="error"
            ).exists(),
            "hide_notifications": hide,
        },
    )


# AC-16 & AC-16(2): Allows authorized administrators to modify user groups and roles
@permission_required("otto.manage_users")
def manage_users(request):
    if request.method == "POST":
        form = UserGroupForm(request.POST)
        if form.is_valid():
            # The form contains users (multiple choice, named "email" but value is "id")
            # and groups (multiple choice, named "group" but value is "id")
            # We want to add the selected groups to the selected users
            users = form.cleaned_data["email"]
            groups = form.cleaned_data["group"]
            for user in users:
                logger.info("Updating user groups", user=user, groups=groups)
                user.groups.clear()
                user.groups.add(*groups)
                if "pilot" in form.cleaned_data:
                    user.pilot = form.cleaned_data["pilot"]
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
            initial={"email": [user], "group": user.groups.all(), "pilot": user.pilot}
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
                        logger.error(f"Invalid email address {upn}: {e}")
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
                    user, created = User.objects.get_or_create(upn=upn)
                    if created:
                        user.email = email
                        user.first_name = given_name
                        user.last_name = surname
                        if pilot_id:
                            user.pilot = pilot
                        user.save()
                    if not created:
                        user.groups.clear()
                        if pilot_id:
                            user.pilot = pilot
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
    writer.writerow(["upn", "pilot_id", "roles"])

    # Only get users who have roles
    for user in User.objects.filter(groups__isnull=False).order_by("last_name"):
        roles = "|".join(user.groups.values_list("name", flat=True))
        pilot_id = user.pilot.pilot_id if user.pilot else ""
        writer.writerow([user.upn, pilot_id, roles])

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
        pilot = Pilot.objects.get(id=pilot_id)
        pilot.delete()
        response = HttpResponse()
        # Add hx-redirect header to trigger HTMX redirect
        response["hx-redirect"] = reverse("manage_pilots")
        return response
    if pilot_id:
        pilot = Pilot.objects.get(id=pilot_id)
        form = PilotForm(instance=pilot)
    else:
        form = PilotForm()
    return render(request, "components/pilot_modal.html", {"form": form})


def aggregate_costs(costs, x_axis="day"):
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

    # Get the filters / groupings from the query string
    x_axis = request.GET.get("x_axis", "day")
    group = request.GET.get("group", "feature")
    bar_chart_type = request.GET.get("bar_chart_type", "stacked")
    pilot = request.GET.get("pilot", "all")
    feature = request.GET.get("feature", "all")
    cost_type = request.GET.get("cost_type", "all")

    # First, filter the costs by the selected options
    raw_costs = Cost.objects.all()
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
    total_costs_alltime = display_cad_cost(sum(c.usd_cost for c in raw_costs))

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

    costs = aggregate_costs(raw_costs, x_axis)
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
        "secondary_number": total_costs_alltime,
        "secondary_number_title": _("All time"),
        "chart_x_labels": chart_x_labels,
        "chart_y_groups": chart_y_groups,
        "x_axis": x_axis,
        "group": group,
        "bar_chart_type": bar_chart_type,
        "pilot": pilot,
        "feature": feature,
        "cost_type": cost_type,
        "x_axis_options": x_axis_labels,
        "group_options": group_labels,
        "bar_chart_type_options": bar_chart_type_labels,
        "pilot_options": pilot_options,
        "feature_options": feature_options,
        "cost_type_options": cost_type_options,
    }
    return render(request, "cost_dashboard.html", context)
