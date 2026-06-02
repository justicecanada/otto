from hashlib import md5

from django.conf import settings
from django.core.cache import cache

from structlog import get_logger

from otto.forms import CostGroupSelectionForm
from otto.models import CostGroup

logger = get_logger(__name__)


def _safe_cache_get(key, default=None):
    """Return cache values without letting debug-toolbar cache instrumentation crash the page."""
    if getattr(settings, "DEBUG_TOOLBAR", False):
        return default

    try:
        return cache.get(key, default)
    except RecursionError:
        logger.warning(
            "Cache get recursion detected in context processor.",
            cache_key=key,
        )
        return default


def otto_version(request):
    environment = settings.ENVIRONMENT.lower()
    if not settings.OTTO_BUILD_DATE:
        version_html = environment
    else:
        hash = settings.OTTO_VERSION_HASH
        hash_github_url = f"https://github.com/justice-bac/otto/commit/{hash}"
        build_date = settings.OTTO_BUILD_DATE.strftime("%Y-%m-%d")
        version_html = f"""
        <small class="d-none" id="otto-version">
            v{build_date}/{environment}
            <a href="{hash_github_url}">Hidden GitHub Link</a>
        </small>
        """
    context = {
        "environment": environment,
        "otto_version": version_html,
        "load_test_enabled": _safe_cache_get("load_testing_enabled", False),
    }
    message_from_admins = _safe_cache_get("message_from_admins", None)
    if message_from_admins:
        category = message_from_admins.get("category", "info")
        message = (
            "message_fr"
            if getattr(request, "LANGUAGE_CODE", "en") == "fr"
            else "message_en"
        )
        context["message_from_admins"] = message_from_admins.get(message, "")
        context["message_from_admins_category"] = category
        # Hash for session-based dismiss tracking
        context["message_from_admins_hash"] = md5(
            context["message_from_admins"].encode()
        ).hexdigest()[:8]

    # Add cost group switcher data if user is authenticated
    if request.user.is_authenticated:
        context["active_cost_group"] = request.user.get_active_cost_group(request)
        context["cost_groups"] = CostGroup.get_available_cost_groups(request.user)
        context["cost_group_selection_form"] = CostGroupSelectionForm()

    return context
