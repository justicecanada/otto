import os
from datetime import datetime
from unittest.mock import MagicMock

from django.contrib.auth.models import Group
from django.core.management import call_command

import pytest
import pytest_asyncio
from asgiref.sync import sync_to_async
from PIL import Image
from reportlab.pdfgen import canvas

pytest_plugins = ("pytest_asyncio",)


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
            from django.conf import settings

            if not settings.IS_RUNNING_IN_GITHUB:
                call_command("load_corporate_library")

    return await sync_to_async(_inner)()


@pytest.fixture()
def all_apps_user(db, django_user_model):
    def new_user(username="all_apps_user"):
        user = django_user_model.objects.create_user(
            upn=f"{username}_upn",
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
            upn=f"{username}_upn",
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
