from functools import wraps

from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _

from structlog import get_logger

from otto.models import Notification
from otto.rules import ADMINISTRATIVE_PERMISSIONS
from otto.utils.common import robust_redirect

logger = get_logger(__name__)


def otto_user_required(func):
    """AC-3: Require membership in the 'Otto user' group.

    Caches the group check on the request object so that multiple
    decorators in the same request don't hit the DB again.
    """

    @wraps(func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            logger.info("User is not authenticated", category="security")
            return robust_redirect(request, reverse("index"))

        # Cache the result on the request to avoid repeated DB queries
        if not hasattr(request, "_is_otto_user"):
            request._is_otto_user = request.user.groups.filter(
                name__in=[settings.OTTO_USER_GROUP, settings.OTTO_ADMIN_GROUP]
            ).exists()

        if not request._is_otto_user:
            logger.info(
                "User is not in Otto user group",
                category="security",
            )
            Notification.objects.create(
                user=request.user,
                heading=_("Access controls"),
                text=_("You are not authorized to access this application."),
                category="error",
            )
            return robust_redirect(request, reverse("index"))

        return func(request, *args, **kwargs)

    return wrapper


# AC-3: Enforce access controls on specific views and functions
def permission_required(
    perm,
    fn=None,
    login_url=None,
    raise_exception=False,
    redirect_field_name=REDIRECT_FIELD_NAME,
    log=True,  # New argument to control logging
):
    # Modification of rules.contrib.views.permission_required
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            # Normalize to a list of permissions
            if isinstance(perm, str):
                perms = (perm,)
            else:
                perms = perm

            # Get the object to check permissions against
            if callable(fn):
                obj = fn(request, *args, **kwargs)
            else:  # pragma: no cover
                obj = fn

            # Get the user
            user = request.user

            # Check for permissions and return a response
            if not user.has_perms(perms, obj):
                if log:
                    logger.info(
                        "User does not have permission",
                        admin=bool(ADMINISTRATIVE_PERMISSIONS.intersection(perms)),
                        category="security",
                        path=request.path,
                        perms=perms,
                    )
                # User does not have a required permission
                if raise_exception:
                    raise PermissionDenied()
                # User does not have required permission to edit library so redirect to their personal library
                elif perms.__contains__("librarian.edit_library"):
                    return redirect(
                        reverse(
                            "librarian:modal_view_library",
                            kwargs={"library_id": user.personal_library.id},
                        )
                    )
                else:
                    Notification.objects.create(
                        user=user,
                        heading="Access controls",
                        text=_("Unauthorized access of URL:") + f" {request.path}",
                        category="error",
                    )
                    return robust_redirect(request, reverse("index"))
            else:
                # User has all required permissions -- allow the view to execute
                if bool(ADMINISTRATIVE_PERMISSIONS.intersection(perms)) and log:
                    logger.info(
                        "Administrative access granted",
                        admin=True,
                        category="security",
                        path=request.path,
                        perms=perms,
                    )
                return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator


def budget_required(func):
    @wraps(func)
    def wrapper(request, *args, **kwargs):
        def _over_budget_response(message_text):
            Notification.objects.create(
                user=request.user,
                heading=_("Budget limit"),
                text=message_text,
                category="error",
            )
            if request.headers.get("HX-Request"):
                response = HttpResponse(status=200)
                response["HX-Redirect"] = request.headers.get("HX-Current-URL")
            else:
                response = HttpResponseRedirect(reverse("index"))
            return response

        active_cost_group = request.user.get_active_cost_group(request)
        if active_cost_group:
            if active_cost_group.is_over_budget:
                logger.info(
                    "Cost group blocked due to budget overage",
                    category="budget",
                    cost_group_id=active_cost_group.cost_group_id,
                )
                return _over_budget_response(
                    _(
                        "The selected cost group has reached its monthly budget limit. Please contact an Otto administrator or wait until the 1st for the limit to reset."
                    )
                )
            return func(request, *args, **kwargs)

        if request.user.is_over_budget:
            logger.info("User blocked due to budget overage", category="budget")
            return _over_budget_response(
                _(
                    "You have reached your monthly budget limit. Please contact an Otto administrator or wait until the 1st for the limit to reset."
                )
            )

        return func(request, *args, **kwargs)

    return wrapper
