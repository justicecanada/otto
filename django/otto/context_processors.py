from django.conf import settings
from django.core.cache import cache


def otto_version(request):
    environment = settings.ENVIRONMENT.lower()
    if not settings.OTTO_BUILD_DATE:
        version_html = environment
    else:
        hash = settings.OTTO_VERSION_HASH
        hash_github_url = f"https://github.com/justicecanada/otto/commit/{hash}"
        # Get just the date from the datetime object
        build_date = settings.OTTO_BUILD_DATE.strftime("%Y-%m-%d")
        version_html = f"""
        <small class="d-none" id="otto-version">
            v{build_date}/{environment}
            <a href="{hash_github_url}">Hidden GitHub Link</a>
        </small>
        """
    return {
        "environment": environment,
        "otto_version": version_html,
        "load_test_enabled": cache.get("load_testing_enabled", False),
    }
