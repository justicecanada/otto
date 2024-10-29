import os
import shutil
from datetime import datetime
from unittest.mock import MagicMock

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import override_settings

import pytest
import pytest_asyncio
from asgiref.sync import sync_to_async
from PIL import Image
from reportlab.pdfgen import canvas

pytest_plugins = ("pytest_asyncio",)


@pytest.fixture(scope="function", autouse=True)
def set_test_media():
    # Define the test media directory
    test_media_dir = os.path.join(settings.BASE_DIR, "test_media")

    storages = settings.STORAGES.copy()
    storages["default"]["LOCATION"] = test_media_dir

    # Ensure the test media directory is clean
    if os.path.exists(test_media_dir):
        shutil.rmtree(test_media_dir)
    os.makedirs(test_media_dir)

    # Use override_settings to set MEDIA_ROOT
    with override_settings(STORAGES=storages, MEDIA_ROOT=test_media_dir):
        yield  # This allows the tests to run

    # Cleanup after tests
    shutil.rmtree(test_media_dir)


@pytest_asyncio.fixture(scope="session")
async def django_db_setup(django_db_setup, django_db_blocker):
    def _inner():
        with django_db_blocker.unblock():
            call_command(
                "reset_app_data",
                "groups",
                "apps",
                "terms",
                "security_labels",
                "library_mini",
                "cost_types",
            )
            # Process the Wikipedia document only
            from chat.llm import OttoLLM
            from librarian.models import Document
            from librarian.tasks import process_document_helper

            test_document = Document.objects.get(
                url="https://en.wikipedia.org/wiki/Glyph"
            )
            process_document_helper(test_document, OttoLLM())

    return await sync_to_async(_inner)()


@pytest.fixture()
def all_apps_user(db, django_user_model):
    def new_user(username="all_apps_user"):
        user = django_user_model.objects.create_user(
            upn=f"{username}.lastname@example.com",
            oid=f"{username}_oid",
            email=f"{username}@example.com",
        )
        user.groups.add(Group.objects.get(name="Otto admin"))
        # Accept the terms
        user.accepted_terms_date = datetime.now()
        user.save()
        return user

    return new_user


@pytest.fixture()
def basic_user(db, django_user_model):
    def new_user(username="basic_user", accept_terms=False):
        user = django_user_model.objects.create_user(
            upn=f"{username}.lastname@example.com",
            oid=f"{username}_oid",
            email=f"{username}@example.com",
            accepted_terms_date=datetime.now() if accept_terms else None,
        )
        return user

    return new_user


@pytest.fixture
def mock_pdf_file():
    filename = "temp_file1.pdf"
    c = canvas.Canvas(filename)
    for i in range(3):  # Create 3 pages
        c.drawString(100, 100, f"Page {i+1}")
        c.showPage()
    c.save()

    # Open the file in binary read mode and return the file object
    with open(filename, "rb") as f:
        yield f
    os.remove(filename)


@pytest.fixture
def mock_pdf_file2():
    filename = "temp_file2.pdf"
    c = canvas.Canvas(filename)
    for i in range(10):  # Create 3 pages
        c.drawString(100, 100, f"Page {i+1}")
        c.showPage()
    c.save()

    # Open the file in binary read mode and return the file object
    with open(filename, "rb") as f:
        yield f
    os.remove(filename)


@pytest.fixture
def mock_image_file(filename="temp_image.jpg"):
    mock_file = MagicMock()
    mock_file.name = filename
    yield mock_file


@pytest.fixture
def mock_image_file2():
    img = Image.new("RGB", (1000, 500), "white")
    return img


@pytest.fixture
def mock_unsupported_file():
    mock_file = MagicMock()
    mock_file.name = "temp_unsupported.txt"
    yield mock_file


# Mocking file objects with a .name attribute
class MockFile:
    def __init__(self, name, total_page_num):
        self.name = name
