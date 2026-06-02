import os
import threading
import time
from importlib import import_module
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.messages import get_messages
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.deprecation import MiddlewareMixin

from structlog import get_logger

from otto.models import Visitor

engine = import_module(settings.SESSION_ENGINE)
logger = get_logger(__name__)

_inflight_lock = threading.Lock()
_inflight_requests = 0


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
    # Skip session extension for high-frequency chunked-upload requests.
    # Each TUS chunk is a separate HTTP request; extending the session on every
    # chunk creates a dead tuple in django_session per chunk per user.
    _NO_EXTEND_PREFIXES = ["/upload/", "/api/"]
    _NO_EXTEND_PATHS = ["/user_cost", "/healthz", "/metrics"]
    # Only write the session (and create a DB dead tuple) at most once per 10 min.
    _EXTEND_INTERVAL_SECONDS = 600

    def process_response(
        self, request: HttpRequest, response: HttpResponse
    ) -> HttpResponse:
        path = request.path.rstrip("/") or "/"
        if path in self._NO_EXTEND_PATHS:
            return response
        for prefix in self._NO_EXTEND_PREFIXES:
            if request.path.startswith(prefix):
                return response

        now = timezone.now()
        last_activity_str = request.session.get("last_activity")
        if last_activity_str:
            try:
                from datetime import datetime
                from datetime import timezone as dt_timezone

                last_activity = datetime.fromisoformat(last_activity_str)
                if last_activity.tzinfo is None:
                    last_activity = last_activity.replace(tzinfo=dt_timezone.utc)
                if (
                    now - last_activity
                ).total_seconds() < self._EXTEND_INTERVAL_SECONDS:
                    return response
            except (ValueError, TypeError):
                pass  # malformed timestamp — fall through to update

        # session.get_expire_age() does not return the correct value, so we track
        # the last activity time ourselves
        request.session["last_activity"] = str(now)
        request.session.set_expiry(settings.SESSION_COOKIE_AGE)
        return response


class PreventConcurrentLoginsMiddleware(MiddlewareMixin):
    """
    Prevent multiple concurrent logins for a single user.
    Adapted from https://github.com/pcraston/django-preventconcurrentlogins/blob/master/preventconcurrentlogins/middleware.py
    """

    def process_request(self, request):
        if request.user.is_authenticated:
            key_from_cookie = request.session.session_key
            if hasattr(request.user, "visitor"):
                session_key_in_visitor_db = request.user.visitor.session_key
                if session_key_in_visitor_db != key_from_cookie:
                    # Delete the Session object from database and cache
                    engine.SessionStore(session_key_in_visitor_db).delete()
                    request.user.visitor.session_key = key_from_cookie
                    request.user.visitor.save()
            else:
                Visitor.objects.create(user=request.user, session_key=key_from_cookie)


class TimezoneMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if request.user.is_authenticated:
            tzname = request.COOKIES.get("otto-timezone")
            if tzname:
                try:
                    timezone.activate(ZoneInfo(tzname))
                    return
                except Exception:
                    timezone.deactivate()
            else:
                timezone.deactivate()
        return self.get_response(request)


class RequestPressureMiddleware(MiddlewareMixin):
    """
    Adds lightweight request pressure signal logging, with emphasis on /healthz latency.
    """

    def process_request(self, request):
        global _inflight_requests
        request._request_started_perf = time.perf_counter()
        with _inflight_lock:
            _inflight_requests += 1
            request._inflight_at_start = _inflight_requests

    def process_response(
        self, request: HttpRequest, response: HttpResponse
    ) -> HttpResponse:
        global _inflight_requests
        start = getattr(request, "_request_started_perf", None)
        elapsed_ms = None
        if start is not None:
            elapsed_ms = int((time.perf_counter() - start) * 1000)

        with _inflight_lock:
            _inflight_requests = max(0, _inflight_requests - 1)
            inflight_now = _inflight_requests

        path = request.path or ""
        health_warn_ms = int(os.getenv("OTTO_HEALTHZ_WARN_MS", "2000"))

        if path.startswith("/healthz"):
            if elapsed_ms is not None and (
                elapsed_ms >= health_warn_ms or response.status_code >= 500
            ):
                logger.warning(
                    "healthz_slow_or_unhealthy",
                    path=path,
                    status_code=response.status_code,
                    elapsed_ms=elapsed_ms,
                    inflight_at_start=getattr(request, "_inflight_at_start", None),
                    inflight_after_response=inflight_now,
                )

        return response
