import os

import django
from django.urls import resolve

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "otto.settings")
django.setup()
resolve("/")
