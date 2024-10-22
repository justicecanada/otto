import json
import tempfile

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

import pytest
from asgiref.sync import sync_to_async

from chat.llm import OttoLLM
from chat.models import Chat, ChatFile, Message
from chat.utils import htmx_stream, title_chat
from librarian.models import Library
from otto.models import App, Notification, SecurityLabel

pytest_plugins = ("pytest_asyncio",)
skip_on_github_actions = pytest.mark.skipif(
    settings.IS_RUNNING_IN_GITHUB, reason="Skipping tests on GitHub Actions"
)

skip_on_devops_pipeline = pytest.mark.skipif(
    settings.IS_RUNNING_IN_DEVOPS, reason="Skipping tests on DevOps Pipelines"
)
