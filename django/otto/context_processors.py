from django.conf import settings
from django.core.cache import cache


def otto_version(request):
    environment = settings.ENVIRONMENT.lower()
    if not settings.OTTO_BUILD_DATE:
        version_html = environment
    else:
        hash = settings.OTTO_VERSION_HASH
        hash_github_url = f"https://github.com/justicecanada/otto/commit/{hash}"
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
        "load_test_enabled": cache.get("load_testing_enabled", False),
    }
    message_from_admins = cache.get("message_from_admins", None)
    if message_from_admins:
        category = message_from_admins.get("category", "info")
        message = (
            "message_fr"
            if getattr(request, "LANGUAGE_CODE", "en") == "fr"
            else "message_en"
        )
        context["message_from_admins"] = message_from_admins.get(message)
        context["message_from_admins_category"] = category
    return context
