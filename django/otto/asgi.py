"""
ASGI config for otto project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.1/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

from channels.routing import ProtocolTypeRouter

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "otto.settings")

# Get the Django ASGI application
django_asgi_app = get_asgi_application()

HEALTH_CHECK_PATHS = {"/healthz", "/healthz/"}
HEALTH_CHECK_BODY = b'{"status":"ok"}'


async def http_application(scope, receive, send):
    if scope.get("path") in HEALTH_CHECK_PATHS:
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(HEALTH_CHECK_BODY)).encode()),
            (b"cache-control", b"no-store"),
        ]
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"" if scope.get("method") == "HEAD" else HEALTH_CHECK_BODY,
            }
        )
        return

    await django_asgi_app(scope, receive, send)


# Define an ASGI application using ProtocolTypeRouter
application = ProtocolTypeRouter(
    {
        "http": http_application,
    }
)
