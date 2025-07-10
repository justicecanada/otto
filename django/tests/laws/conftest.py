from django.core.management import call_command

import pytest_asyncio
from asgiref.sync import sync_to_async


@pytest_asyncio.fixture(scope="session")
async def django_db_setup(django_db_setup, django_db_blocker):
    def _inner():
        with django_db_blocker.unblock():
            call_command("load_laws_xml", "--small", "--start", "--reset")

    return await sync_to_async(_inner)()
