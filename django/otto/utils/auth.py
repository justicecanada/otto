from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse

from structlog import get_logger

logger = get_logger(__name__)


def map_entra_to_django_user(**fields):
    """
    Used by the Django-Azure-Auth library to map OAuth fields to the Django User model.
    The USERNAME_FIELD is already mapped in settings.py: AZURE_AUTH["USERNAME_ATTRIBUTE"]
    """
    debug_output = "\n ----------------------------- \n OAuth response from Entra: \n ----------------------------- \n"
    for field in fields:
        debug_output += f"{field}: {fields[field]}\n"

    logger.debug(debug_output)

    django_user = {
        # UPN is already mapped in settings.py
        # "upn": fields["userPrincipalName"],
        "email": fields["mail"],
        "first_name": fields["givenName"],
        "last_name": fields["surname"],
        "oid": fields["oid"],
    }
    return django_user


PUBLIC_PATHS = [
    "/azure_auth/login",
    "/azure_auth/logout",
    "/accounts/login/callback",
    "/welcome",
    "/notifications",
    "/healthz",
    "/load_test",
]

NO_TERMS_PATHS = PUBLIC_PATHS + [
    "/",
    "/terms_of_use",
    "/i18n/setlang",
    "/feedback",
    "/user_cost",
    "/user_management/mark_tour_completed/homepage",
    "/user_management/mark_tour_completed/ai_assistant",
    "/user_management/mark_tour_completed/laws",
    "/user_management/reset_completion_flags",
]


class RedirectToLoginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        no_trailing_dash_path = request.path.rstrip("/") or "/"
        if no_trailing_dash_path in PUBLIC_PATHS:
            return self.get_response(request)
        # AC-2, AC-19: User Authentication (Can't be anonymous)
        if not request.user.is_authenticated or request.user.is_anonymous:
            if request.headers.get("HX-Request"):
                response = HttpResponse(status=200)
                response["HX-Redirect"] = reverse("welcome")
                return response
            return HttpResponseRedirect(reverse("welcome") + "?next=" + request.path)
        return self.get_response(request)


class AcceptTermsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        no_trailing_dash_path = request.path.rstrip("/") or "/"
        if no_trailing_dash_path in NO_TERMS_PATHS:
            return self.get_response(request)
        if not request.user.accepted_terms:
            return HttpResponseRedirect(
                reverse("terms_of_use") + "?next=" + request.path
            )

        return self.get_response(request)
