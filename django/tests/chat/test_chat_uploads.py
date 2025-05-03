import asyncio
import tempfile
from unittest import mock

from django.conf import settings
from django.contrib import messages
from django.urls import reverse
from django.utils import timezone

import pytest
from asgiref.sync import async_to_sync, sync_to_async

from chat.forms import PresetForm
from chat.llm import OttoLLM
from chat.models import Chat, ChatFile, ChatOptions, Message, Preset
from chat.utils import htmx_stream, title_chat
from librarian.models import Library
from otto.models import App, Notification, SecurityLabel

pytest_plugins = ("pytest_asyncio",)


async def final_response_helper(stream):
    content = b""
    async for chunk in stream:
        content = chunk
    return content


def final_response(stream):
    return asyncio.run(final_response_helper(stream))


"""
TODO: Write tests for chat uploads, including librarian uploads

In one chat:
- Single file
- Multiple files
- File with same name and same hash
- File with same name and different hash
- File with different name and same hash

In new chat:
- File that exists in different data source (i.e. chat) but has same hash

Delete first chat
- SavedFile for file uploaded to second chat should still exist
- All other SavedFiles should be deleted

Manually delete file on disk (test "fix missing file" case)
- Upload file again to same chat
- Ensure same SavedFile is used
- Ensure that file on disk is re-created and associated with the same SavedFile

Uploading zips:
- Upload zip with files inside
- Ensure SavedFile is created for each file within the zip
- Original ZIP should be deleted ?
- Re-upload zip with same files
- Ensure same SavedFiles are used

Delete all chats
- Ensure all SavedFiles are deleted
- Ensure all files on disk are deleted
"""
