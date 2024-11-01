from django.conf import settings


def otto_version(request):
    if not settings.OTTO_BUILD_DATE:
        return {"otto_version": settings.ENVIRONMENT.lower()}
    hash = settings.OTTO_VERSION_HASH
    hash_github_url = f"https://github.com/justicecanada/otto/commit/{hash}"
    # Get just the date from the datetime object
    build_date = settings.OTTO_BUILD_DATE.strftime("%Y-%m-%d")
    version_html = f"""
    <small class="text-muted {'d-none' if not request.user.has_perm("otto.view_github_link") else ''}"
           id="otto-version">
        <a href="{hash_github_url}" target="_blank" class="text-muted text-decoration-none">
            v{build_date}</a>/{settings.ENVIRONMENT.lower()}
    </small>
    """
    return {"otto_version": version_html}