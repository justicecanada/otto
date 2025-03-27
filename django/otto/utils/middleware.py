from django.conf import settings
from django.contrib.messages import get_messages
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin


# Code referenced: https://github.com/bblanchon/django-htmx-messages-framework/tree/oob
class HtmxMessageMiddleware(MiddlewareMixin):
    """
    Middleware that moves messages into the HX-Trigger header when request is made with HTMX
    """

    def process_response(
        self, request: HttpRequest, response: HttpResponse
    ) -> HttpResponse:

        # The HX-Request header indicates that the request was made with HTMX
        if "HX-Request" not in request.headers:
            return response

        # Ignore HTTP redirections because HTMX cannot read the body
        if 300 <= response.status_code < 400:
            return response

        # Ignore client-side redirection because HTMX drops OOB swaps
        if "HX-Redirect" in response.headers:
            return response

        # Extract the messages
        messages = get_messages(request)
        if not messages:
            return response

        response.write(
            render_to_string(
                template_name="components/toasts.html",
                context={"messages": messages},
                request=request,
            )
        )

        return response


class ExtendSessionMiddleware(MiddlewareMixin):
    def process_response(
        self, request: HttpRequest, response: HttpResponse
    ) -> HttpResponse:
        no_extend_paths = ["/user_cost"]
        no_trailing_dash_path = request.path.rstrip("/") or "/"
        if not (no_trailing_dash_path in no_extend_paths):
            # session.get_expire_age() does not return the correct value, so we track
            # the last activity time ourselves
            request.session["last_activity"] = str(timezone.now())
            request.session.set_expiry(settings.SESSION_COOKIE_AGE)
        return response
