from django.conf import settings


def otto_version(request):
    return {"otto_version": f"{settings.OTTO_VERSION}/{settings.ENVIRONMENT.lower()}"}
