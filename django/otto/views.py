import csv
import io
from collections import defaultdict

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import validate_email
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import check_for_language, get_language
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from azure_auth.views import azure_auth_login as azure_auth_login
from structlog import get_logger

from chat.models import Message
from otto.forms import FeedbackForm, UserGroupForm
from otto.metrics.activity_metrics import otto_access_total
from otto.metrics.feedback_metrics import otto_feedback_submitted_with_comment_total
from otto.models import App, Feature, UsageTerm
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
        logger.info("Feedback form submitted", message_id=message_id)
        chat_message = Message.objects.get(id=message_id)
        mode = chat_message.chat.options.mode
        form = FeedbackForm(request.user, message_id, chat_mode=mode, data=request.POST)

        if form.is_valid():
            date_and_time = timezone.now().strftime("%Y%m%d-%H%M%S")
            form.cleaned_data["created_at"] = date_and_time
            form.save()

            otto_feedback_submitted_with_comment_total.labels(
                user=request.user.upn
            ).inc()

            if message_id is None:
                return redirect("feedback_success")
            else:
                return HttpResponse()
    else:
        chat_message = Message.objects.get(id=message_id)
        mode = chat_message.chat.options.mode
        form = FeedbackForm(request.user, message_id, chat_mode=mode)

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
        form = UserGroupForm(initial={"email": [user], "group": user.groups.all()})
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
                    user, created = User.objects.get_or_create(upn=upn)
                    if created:
                        user.email = email
                        user.first_name = given_name
                        user.last_name = surname
                        user.save()
                    if not created:
                        user.groups.clear()
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
    writer.writerow(["upn", "roles"])

    for user in User.objects.all():
        roles = "|".join(user.groups.values_list("name", flat=True))
        writer.writerow([user.upn, roles])

    return response
