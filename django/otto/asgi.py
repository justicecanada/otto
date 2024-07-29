"""
ASGI config for otto project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.1/howto/deployment/asgi/
"""

import logging
import os

from django.conf import settings
from django.core.asgi import get_asgi_application

from channels.routing import ProtocolTypeRouter

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "otto.settings")

# Get the Django ASGI application
django_asgi_app = get_asgi_application()

# Define an ASGI application using ProtocolTypeRouter
application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
    }
)
