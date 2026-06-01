import io
import os
import shutil
import uuid
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import override_settings

import pytest
import pytest_asyncio
from asgiref.sync import sync_to_async
from docx import Document
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from reportlab.pdfgen import canvas

from text_extractor.models import OutputFile

pytest_plugins = ("pytest_asyncio",)

this_dir = os.path.dirname(os.path.abspath(__file__))


def _drop_stale_test_databases_if_needed(settings):
    """
    Auto-drop test databases if schema is stale (e.g., after switching branches).

    When using --reuse-db, the test database persists between runs. If you switch
    to a branch with different migrations, the database schema becomes out of sync,
    causing IntegrityError (e.g., "null value in column X violates not-null constraint").

    This function detects schema mismatches by comparing the database's migration
    history with the current migration files, and drops the test database if needed.
    """
    import subprocess

    from django.db.migrations.loader import MigrationLoader

    # Skip in GitHub Actions where DBs are ephemeral
    if os.environ.get("PYTEST_USE_PRODUCTION_DB") == "true":
        return

    for db_alias in ["default", "vector_db"]:
        if db_alias not in settings.DATABASES:
            continue

        db_config = settings.DATABASES[db_alias]
        db_name = db_config.get("NAME", "")

        # Only process test databases
        if not db_name.startswith("test_"):
            continue

        # Check if using PostgreSQL
        engine = db_config.get("ENGINE", "")
        if "postgres" not in engine:
            continue

        host = db_config.get("HOST", "localhost")
        port = db_config.get("PORT", "5432")
        user = db_config.get("USER", "postgres")
        password = db_config.get("PASSWORD", "")

        env = os.environ.copy()
        if password:
            env["PGPASSWORD"] = password

        try:
            # Query django_migrations table directly via psql
            result = subprocess.run(
                [
                    "psql",
                    "-h",
                    host,
                    "-p",
                    str(port),
                    "-U",
                    user,
                    "-d",
                    db_name,
                    "-t",  # tuples only
                    "-c",
                    "SELECT app || ',' || name FROM django_migrations;",
                ],
                env=env,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                # Database doesn't exist or can't connect - that's fine
                continue

            # Parse applied migrations from DB
            db_migrations = set()
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if "," in line:
                    parts = line.split(",", 1)
                    db_migrations.add((parts[0].strip(), parts[1].strip()))

            if not db_migrations:
                continue

            # Get migration files from disk (without DB connection)
            loader = MigrationLoader(None, ignore_no_migrations=True)
            disk_migrations = set(loader.disk_migrations.keys())

            # Check for migrations in DB that don't exist on disk (stale schema)
            stale_migrations = db_migrations - disk_migrations
            if stale_migrations:
                print(
                    f"\n⚠ Stale test database detected for '{db_alias}': "
                    f"Found {len(stale_migrations)} migration(s) not in current code."
                )
                stale_list = sorted(stale_migrations)[:5]
                print(f"  Stale migrations: {stale_list}...")
                print(f"  Dropping '{db_name}' to force fresh schema...\n")

                # Drop the database using psql connected to 'postgres' database
                try:
                    subprocess.run(
                        [
                            "psql",
                            "-h",
                            host,
                            "-p",
                            str(port),
                            "-U",
                            user,
                            "-d",
                            "postgres",
                            "-c",
                            f"DROP DATABASE IF EXISTS {db_name};",
                        ],
                        env=env,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    print(f"✓ Dropped stale test database '{db_name}'\n")
                except subprocess.CalledProcessError as e:
                    print(f"⚠ Could not drop '{db_name}': {e.stderr}")

        except Exception:
            # Any error - just skip, tests will fail with clearer error if DB is bad
            pass


@pytest.fixture(scope="session")
def django_db_modify_db_settings():
    """
    CRITICAL: Force pytest-django to ALWAYS use TEST databases.

    This fixture overrides database settings to ensure the test database name
    is used for BOTH default and vector_db databases. Without this, tests would
    connect to PRODUCTION databases and DELETE real data.

    This runs before any database setup, ensuring all connections use test DBs.

    EXCEPTION: In GitHub Actions, we use the production database names (otto, llama_index)
    because the Celery worker runs in a separate process and needs to share the same
    databases as pytest. Those databases are ephemeral (created fresh for each CI run).
    """
    from django.conf import settings

    # In GitHub Actions with separate Celery worker, skip test DB renaming
    if os.environ.get("PYTEST_USE_PRODUCTION_DB") == "true":
        print(
            "\n⚠ GitHub Actions mode: Using production DB names (otto, llama_index) for tests\n"
        )
        return

    # Force test database names for both databases
    if "vector_db" in settings.DATABASES:
        original_name = settings.DATABASES["vector_db"].get("NAME", "")
        test_name = settings.DATABASES["vector_db"]["TEST"]["NAME"]

        # Override the actual NAME with the TEST name for the entire test session
        settings.DATABASES["vector_db"]["NAME"] = test_name

        print(f"\n✓ Vector DB protection: Changed '{original_name}' → '{test_name}'\n")

    if "default" in settings.DATABASES:
        if "TEST" in settings.DATABASES["default"]:
            original_name = settings.DATABASES["default"].get("NAME", "")
            test_name = settings.DATABASES["default"]["TEST"]["NAME"]
            settings.DATABASES["default"]["NAME"] = test_name
            print(
                f"✓ Default DB protection: Changed '{original_name}' → '{test_name}'\n"
            )

    # Auto-drop stale test databases if schema is out of sync (e.g., after branch switch)
    # This handles IntegrityError issues from leftover columns in --reuse-db mode
    _drop_stale_test_databases_if_needed(settings)


@pytest.fixture(scope="function", autouse=True)
def protect_production_vector_db():
    """
    CRITICAL: Ensure tests NEVER touch the production vector database.

    This fixture:
    1. Verifies we're using a test database
    2. Clears global engine cache to force re-initialization with test DB
    3. Validates the database name before AND after test execution

    EXCEPTION: In GitHub Actions with separate Celery worker, we use ephemeral
    production database names (created fresh for each CI run).
    """
    from chat._llm import vector_store

    # Clear any existing global engines to force re-initialization with test DB
    vector_store._pg_sync_engine = None
    vector_store._pg_async_engine = None

    # Verify we're using test database before test runs
    vector_db_name = settings.DATABASES["vector_db"]["NAME"]

    # In GitHub Actions, allow production DB names (they're ephemeral)
    use_production_db = os.environ.get("PYTEST_USE_PRODUCTION_DB") == "true"

    if not use_production_db and not vector_db_name.startswith("test_"):
        raise RuntimeError(
            f"CRITICAL: Tests attempting to use PRODUCTION vector database '{vector_db_name}'! "
            f"Expected name to start with 'test_'. "
            f"This would DELETE your actual data. Tests BLOCKED."
        )

    yield

    # Clean up after test - clear engines again to prevent cross-test contamination
    vector_store._pg_sync_engine = None
    vector_store._pg_async_engine = None

    # Verify we didn't somehow switch databases during the test
    final_db_name = settings.DATABASES["vector_db"]["NAME"]
    if not use_production_db and not final_db_name.startswith("test_"):
        raise RuntimeError(
            f"CRITICAL: After test execution, database name changed to '{final_db_name}'! "
            f"This should never happen. Investigation needed."
        )


@pytest.fixture(scope="function", autouse=True)
def use_mock_llm_when_no_llm_endpoint(request):
    """
    Enable mock LLM and embedding in tests when no Azure endpoint/key is available.

    In CI we typically avoid live calls; locally we may run against Azure directly.

    This preserves the actual test logic and code paths while avoiding network dependencies.

    Note: Tests that explicitly need real LLM calls (e.g., token counting tests) should
    use their own skipif conditions and this fixture will respect those.
    """
    from django.conf import settings

    from chat._llm.core import mock_llm_context

    # Always get current token to ensure proper cleanup
    # This handles cases where context leaked from previous tests
    test_module = (
        request.node.module.__name__ if hasattr(request.node, "module") else ""
    )
    try:
        mock_llm_context.get()
    except LookupError:
        # No existing context; continue with default handling
        pass
    if "test_streaming_token_capture" in test_module:
        # These tests need real API calls - ensure mock is explicitly disabled
        token = mock_llm_context.set(False)
        try:
            yield
        finally:
            # Always reset, even if test fails
            mock_llm_context.reset(token)
        return

    # If Azure endpoint/key are not set, enable mock LLM context
    if not settings.AZURE_AI_SERVICES_ENDPOINT or not settings.AZURE_AI_SERVICES_KEY:
        token = mock_llm_context.set(True)
        try:
            yield
        finally:
            # Always reset, even if test fails
            mock_llm_context.reset(token)
    else:
        # Real backend is available, don't mock - but ensure context is clean
        token = mock_llm_context.set(False)
        try:
            yield
        finally:
            mock_llm_context.reset(token)


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


@pytest.fixture(scope="function", autouse=False)
def configure_celery_for_tests():
    """
    Run Celery tasks eagerly during tests and unify light/heavy queues so
    chains don't rely on external workers. This avoids changing app code.
    """

    from celery import current_app as celery_app

    # Force eager execution and propagate exceptions to fail fast
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    # celery_app.conf.task_store_eager_result = True

    # Make both queues identical for the duration of tests
    unified_queue = "test"

    # Patch settings via override for tests
    with override_settings(LIGHT_QUEUE=unified_queue, HEAVY_QUEUE=unified_queue):
        yield

    # Restore Celery config (pytest process-scoped, but be explicit)
    celery_app.conf.task_always_eager = False
    celery_app.conf.task_eager_propagates = False


@pytest_asyncio.fixture(scope="session")
async def django_db_setup(django_db_setup, django_db_blocker):
    def _inner():
        with django_db_blocker.unblock():
            call_command(
                "reset_app_data",
                "groups",
                "apps",
                "security_labels",
                "library_mini",
                "cost_types",
                "presets",
                "skills",
            )

            # Reset database sequences to prevent unique constraint violations
            # when using --reuse-db. This ensures auto-increment IDs start fresh.
            from django.core.management.color import no_style
            from django.db import connection

            # Get sequence reset SQL and execute it
            with connection.cursor() as cursor:
                # Get all apps
                from django.apps import apps

                app_configs = apps.get_app_configs()

                # Generate and execute sequence reset SQL for each app
                for app_config in app_configs:
                    # sqlsequencereset returns a list of SQL statements
                    sql_list = connection.ops.sequence_reset_sql(
                        no_style(), app_config.get_models()
                    )
                    for sql in sql_list:
                        cursor.execute(sql)

            from celery import current_app as celery_app

            from otto.models import Cost

            from librarian.models import Document
            from librarian.tasks import process_document

            # Why this block exists:
            # - `process_document.run(...)` can enqueue `finalize_document_light`.
            # - In this session-scoped setup fixture, we are not inside tests that use
            #   `configure_celery_for_tests`, so Celery may not be eager here.
            # - If finalize does not run during setup, the vector table/chunks for the
            #   seeded Canada.ca document may never be created.
            # - Later QA tests (e.g., keyword no-match path) then fail with
            #   `UndefinedTable` before reaching expected user-facing fallback logic.
            #
            # To keep setup deterministic, we temporarily force eager+propagate only
            # around this seed processing call, then restore prior Celery settings.
            test_document = Document.objects.get(url="https://www.canada.ca/en.html")
            previous_always_eager = celery_app.conf.task_always_eager
            previous_eager_propagates = celery_app.conf.task_eager_propagates
            try:
                celery_app.conf.task_always_eager = True
                celery_app.conf.task_eager_propagates = True
                # Ensures both extraction and finalize steps complete in setup.
                process_document.run(document_id=test_document.id, mock_embedding=True)
                # Seeded setup content is useful for search/vector tests, but its
                # incidental Cost rows make cost/dashboard tests depend on hidden
                # global state. Start each test from a clean cost baseline instead.
                Cost.objects.all().delete()
            finally:
                # Avoid leaking Celery mode changes outside this setup section.
                celery_app.conf.task_always_eager = previous_always_eager
                celery_app.conf.task_eager_propagates = previous_eager_propagates

    return await sync_to_async(_inner)()


@pytest.fixture()
def load_example_pdf(django_db_blocker):
    from librarian.models import DataSource, Document, SavedFile
    from librarian.tasks import process_document

    with open(os.path.join(this_dir, "librarian/test_files/example.pdf"), "rb") as f:
        with django_db_blocker.unblock():
            pdf_file = ContentFile(f.read(), name="example.pdf")
            saved_file = SavedFile.objects.create(file=pdf_file)
            d = Document.objects.create(
                saved_file=saved_file,
                filename="example.pdf",
                data_source=DataSource.objects.get(name_en="Canada.ca"),
            )
            process_document.run(document_id=d.id, mock_embedding=True)
            return d


@pytest.fixture()
def all_apps_user(db, django_user_model):
    def new_user(username="all_apps_user"):
        unique_suffix = str(uuid.uuid4())[:8]
        user = django_user_model.objects.create_user(
            upn=f"{username}.lastname.{unique_suffix}@example.com",
            oid=f"{username}_oid_{unique_suffix}",
            email=f"{username}.{unique_suffix}@example.com",
        )
        user.groups.add(Group.objects.get(name="Otto admin"))
        user.groups.add(Group.objects.get(name=settings.OTTO_USER_GROUP))
        # Accept the terms
        user.accepted_terms_date = datetime.now()
        user.save()
        return user

    return new_user


@pytest.fixture()
def basic_user(db, django_user_model):
    def new_user(username="basic_user", accept_terms=False):
        unique_suffix = str(uuid.uuid4())[:8]
        user = django_user_model.objects.create_user(
            upn=f"{username}.lastname.{unique_suffix}@example.com",
            oid=f"{username}_oid_{unique_suffix}",
            email=f"{username}.{unique_suffix}@example.com",
            accepted_terms_date=datetime.now() if accept_terms else None,
        )
        user.groups.add(Group.objects.get(name=settings.OTTO_USER_GROUP))
        return user

    return new_user


@pytest.fixture
def mock_pdf_file():
    filename = "temp_file1.pdf"
    c = canvas.Canvas(filename)
    for i in range(3):  # Create 3 pages
        c.drawString(100, 100, f"Page {i + 1}")
        c.showPage()
    c.save()

    with open(filename, "rb") as f:
        yield f
    os.remove(filename)


@pytest.fixture
def mock_pdf_file2():
    filename = "temp_file2.pdf"
    c = canvas.Canvas(filename)
    for i in range(10):  # Create 10 pages
        c.drawString(100, 100, f"Page {i + 1}")
        c.showPage()
    c.save()

    with open(filename, "rb") as f:
        yield f
    os.remove(filename)


# yields filename and content
@pytest.fixture
def mock_pdf_file3():
    filename = "temp_file1.pdf"
    c = canvas.Canvas(filename)
    for i in range(3):  # Create 3 pages
        c.drawString(100, 100, f"Page {i + 1}")
        c.showPage()
    c.save()

    with open(filename, "rb") as f:
        content = f.read()
        yield filename, content
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


# yields filename and content
@pytest.fixture
def mock_image_file3():
    filename = "temp_image.jpg"
    # Create a simple image
    image = Image.new("RGB", (100, 100), color="red")
    draw = ImageDraw.Draw(image)

    # Draw the letter "R" in black color
    font = ImageFont.load_default()

    draw.text((25, 25), "RIF drawing", fill="black", font=font)
    image.save(filename)

    # Open the file in binary read mode and return the file object and its content
    with open(filename, "rb") as f:
        content = f.read()
        yield filename, content
    os.remove(filename)


@pytest.fixture
def mock_image_file4():
    filename = "temp_image2.jpg"
    # Create a simple image
    image = Image.new("RGB", (49, 49), color="red")
    draw = ImageDraw.Draw(image)

    # Draw the letter "R" in black color
    font = ImageFont.load_default()

    draw.text((10, 10), "tiny image", fill="black", font=font)
    image.save(filename)

    # Open the file in binary read mode and return the file object and its content
    with open(filename, "rb") as f:
        content = f.read()
        yield filename, content
    os.remove(filename)


@pytest.fixture
def mock_unsupported_file():
    mock_file = MagicMock()
    mock_file.name = "temp_unsupported.txt"
    yield mock_file


# Mocking file objects with a .name attribute
class MockFile:
    def __init__(self, name, total_page_num):
        self.name = name


@pytest.fixture
def process_ocr_document_mock(mocker):
    # Mock the Celery task's apply_async method
    mock_apply_async = mocker.patch(
        "text_extractor.views.process_ocr_document.apply_async"
    )
    mock_task = MagicMock()
    mock_task.id = "mock_task_id"
    mock_apply_async.return_value = mock_task

    # Also mock process_document_merge for merged tests
    mock_merge_apply_async = mocker.patch(
        "text_extractor.views.process_document_merge.apply_async"
    )
    mock_merge_task = MagicMock()
    mock_merge_task.id = "mock_merge_task_id"
    mock_merge_apply_async.return_value = mock_merge_task

    # Mock the AsyncResult
    mock_async_result = mocker.patch(
        "text_extractor.views.process_ocr_document.AsyncResult"
    )
    mock_result_instance = MagicMock()
    # Set the return value of result.get()
    mock_result_instance.get.return_value = (
        b"pdf_bytes_content",
        "txt_file_content",
        0.05,
        "input_name",
    )
    mock_async_result.return_value = mock_result_instance

    # Return both OCR and merge mocks
    return {
        "ocr_apply_async": mock_apply_async,
        "merge_apply_async": mock_merge_apply_async,
        "async_result": mock_async_result,
    }


@pytest.fixture
def content_file_mock(mocker):
    original_content_file = ContentFile
    mock_content_file = mocker.patch(
        "django.core.files.base.ContentFile", side_effect=original_content_file
    )
    return mock_content_file


@pytest.fixture
def basic_feedback():
    from django.utils import timezone

    from otto.models import Feedback

    def new_feedback_form(user):
        date_and_time = timezone.now().strftime("%Y%m%d-%H%M%S")
        feedback = Feedback(
            feedback_type=Feedback.FEEDBACK_TYPE_CHOICES[0][0],
            feedback_message="Test Message",
            app="Otto",
            modified_by=user,
            created_by=user,
            created_at=date_and_time,
            modified_on=date_and_time,
            otto_version="v0",
        )
        return feedback

    return new_feedback_form


@pytest.fixture
def output_file():
    pdf_mock = MagicMock()
    pdf_mock.name = "test.pdf"
    pdf_mock.open.return_value.__enter__.return_value.read.return_value = b"PDF content"

    txt_mock = MagicMock()
    txt_mock.name = "test.txt"
    txt_mock.open.return_value.__enter__.return_value.read.return_value = b"TXT content"

    output_file = MagicMock(spec=OutputFile)
    output_file.celery_task_ids = [str(uuid.uuid4())]
    output_file.pdf_file = pdf_mock
    output_file.txt_file = txt_mock
    output_file.file_name = "test_document"
    output_file.usd_cost = 0

    return output_file


@pytest.fixture
def sample_docx():
    doc = Document()
    doc.add_heading("Test Heading", 0)
    doc.add_paragraph("Test paragraph")
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


@pytest.fixture
def sample_pptx():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "Test Slide"
    slide.placeholders[1].text = "Test Content"
    buffer = io.BytesIO()
    prs.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


@pytest.fixture
def sample_excel():
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "Header1"
    ws["B1"] = "Header2"
    ws["A2"] = "Value1"
    ws["B2"] = "Value2"
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


@pytest.fixture
def sample_csv():
    return b"Header1,Header2\nValue1,Value2"


@pytest.fixture
def sample_pdf():
    # Mock PDF content for testing
    return b"%PDF-1.4\n..."


@pytest.fixture(autouse=True)
def ensure_otto_admin_group(db):
    Group.objects.get_or_create(name="Otto admin")
    Group.objects.get_or_create(name=settings.OTTO_USER_GROUP)


@pytest.fixture
def chat_factory():
    def _factory(qa_mode="rag", qa_process_mode="combined_docs"):
        options = SimpleNamespace(
            qa_history=True,
            qa_mode=qa_mode,
            qa_process_mode=qa_process_mode,
            qa_scope="documents",
            qa_model="gpt-4-mini",
            qa_reasoning_effort="medium",
            qa_verbosity="normal",
        )
        options.qa_prompt_combined = SimpleNamespace(
            message_templates=[
                SimpleNamespace(content="SYSTEM TEMPLATE"),
                SimpleNamespace(
                    content="{pre_instructions} :: BODY :: {post_instructions}"
                ),
            ]
        )
        options.qa_pre_instructions = ""
        options.qa_post_instructions = ""
        return SimpleNamespace(options=options, data_source="test-source")

    return _factory


@pytest.fixture
def response_message_factory():
    def _factory(text="What is the update?", message_id=1):
        parent = SimpleNamespace(text=text, sorted_files=[])
        return SimpleNamespace(parent=parent, id=message_id)

    return _factory


@pytest.fixture
def documents_factory():
    def _factory(*names):
        return [SimpleNamespace(name=name) for name in names]

    return _factory


@pytest.fixture
def fake_document_instance_class():
    class FakeDocumentInstance:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.messages_added = []
            self.process_priorities = []

            def add(msg):
                self.messages_added.append(msg)

            self.messages = SimpleNamespace(add=add)

        def process(self, priority):
            self.process_priorities.append(priority)

    return FakeDocumentInstance


@pytest.fixture
def document_stub_factory(fake_document_instance_class):
    def _factory(existing=None):
        class FakeManager:
            def __init__(self, existing_doc):
                self.existing = existing_doc
                self.filter_kwargs = []
                self.created = []

            def filter(self, **kwargs):
                self.filter_kwargs.append(kwargs)
                manager = self

                class FilterResult:
                    def first(inner_self):
                        return manager.existing

                return FilterResult()

            def create(self, **kwargs):
                doc = fake_document_instance_class(**kwargs)
                self.created.append(doc)
                return doc

            def get(self, **kwargs):
                # Simulate .get(id=...) for test
                return self.existing

        manager = FakeManager(existing)
        stub = SimpleNamespace(objects=manager)
        return manager, stub

    return _factory


@pytest.fixture
def DummyResp():
    class DummyResp:
        def __init__(self, status_code=None, headers=None):
            self.status_code = status_code
            self.headers = headers or {}

    return DummyResp


@pytest.fixture
def DummyExc():
    class DummyExc:
        def __init__(
            self,
            status_code=None,
            response=None,
            headers=None,
            message="",
        ):
            # Optional direct status_code
            if status_code is not None:
                self.status_code = status_code
            # Optional response object with status_code/headers
            if response is not None:
                self.response = response
            # Optional direct headers
            if headers is not None:
                self.headers = headers
            self._message = message

        def __str__(self):
            return self._message

    return DummyExc


@pytest.fixture
def FakeDoc():
    class FakeDoc:
        def __init__(
            self,
            filename,
            status,
            message_count,
            is_container=False,
            status_details=None,
        ):
            self.filename = filename
            self.status = status
            self.is_container = is_container
            self.status_details = status_details
            self.messages = type(
                "FakeMessages", (), {"count": lambda self: message_count}
            )()

    return FakeDoc


@pytest.fixture
def FakeManager():
    class InProgressQuerySet:
        def __init__(self, manager):
            self.manager = manager

        def count(self):
            if self.manager.in_progress_counts:
                return self.manager.in_progress_counts.pop(0)
            return 0

    class MessageQuerySet:
        def __init__(self, items):
            self.items = list(items)

        def exclude(self, **kwargs):
            items = self.items
            if "is_container" in kwargs:
                value = kwargs["is_container"]
                items = [doc for doc in items if doc.is_container != value]
            return MessageQuerySet(items)

        def filter(self, **kwargs):
            items = self.items
            for key, value in kwargs.items():
                if key == "status":
                    items = [doc for doc in items if doc.status == value]
            return MessageQuerySet(items)

        def count(self):
            return len(self.items)

        def __iter__(self):
            return iter(self.items)

    class FakeManager:
        def __init__(self, docs, in_progress_counts):
            self.docs = docs
            self.in_progress_counts = list(in_progress_counts)

        def filter(self, **kwargs):
            if "status__in" in kwargs:
                return InProgressQuerySet(self)
            if "messages" in kwargs:
                return MessageQuerySet(self.docs)
            raise AssertionError(f"Unexpected filter kwargs: {kwargs}")

    return FakeManager


@pytest.fixture
def DummyLaw():
    class DummyLaw:
        def __init__(self):
            self.id = 999
            self.pk = 999
            self.eng_law_id = None
            self.node_id_en = "node-en"
            self.node_id_fr = "node-fr"
            self.sha_256_hash_en = None
            self.sha_256_hash_fr = None
            self.saved = False

        def save(self):
            self.saved = True

    return DummyLaw


@pytest.fixture
def DummyLLM():
    class DummyLLM:
        def __init__(self, mock_embedding, priority):
            self.mock_embedding = mock_embedding
            self.priority = priority
            self.index_requests = []

        def get_index(self, name, hnsw):
            self.index_requests.append((name, hnsw))
            return {"name": name, "hnsw": hnsw}

    return DummyLLM


@pytest.fixture
def DummyProgress():
    class DummyProgress:
        def __init__(self, *args, **kwargs):
            self._calls = {}
            # Accept name as first positional argument for compatibility
            if args:
                self._calls["progress_name"] = args[0]
            else:
                self._calls["progress_name"] = kwargs.get("name", None)

        def clear(self):
            self._calls["progress_cleared"] = True

    return DummyProgress


@pytest.fixture
def DummyCostQueryset():
    class DummyCostQueryset:
        def aggregate(self, *a, **k):
            return {"usd_cost__sum": 0.25}

    return DummyCostQueryset


@pytest.fixture
def DummyCostManager(DummyCostQueryset):
    class DummyCostManager:
        def filter(self, *a, **k):
            return DummyCostQueryset()

    return DummyCostManager


@pytest.fixture
def DummyCost(DummyCostManager):
    class DummyCost:
        objects = DummyCostManager()

    return DummyCost


@pytest.fixture
def DummySelf():
    class DummySelf:
        def __init__(self):
            self.request = type("Req", (), {"delivery_info": {"priority": 1}})()

    return DummySelf


@pytest.fixture
def DummyResult():
    class DummyResult:
        id = "fake-task-id"

    return DummyResult


# --- Laws loading test helpers ---
class DummyResponse:
    def __init__(self):
        self.status_code = 200
        self.iter_content_called = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=1):
        self.iter_content_called = True
        # Simulate 3 chunks
        for _ in range(3):
            yield b"abc" * 10


class DummyZip:
    def __init__(self, file, mode):
        self.file = file
        self.mode = mode
        self._namelist = ["file1.txt", "file2.txt"]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def namelist(self):
        return self._namelist

    def extract(self, member, path):
        # Simulate extraction by writing a file
        import pathlib

        tmp_path = pathlib.Path(path)
        (tmp_path / member).write_text("data")


# ============================================================================
# Manual test support
# ============================================================================


def pytest_addoption(parser):
    """Add --manual option for running manual tests."""
    parser.addoption(
        "--manual",
        action="store_true",
        default=False,
        help="Run manual tests that require real API access",
    )


def pytest_collection_modifyitems(config, items):
    """Skip manual tests unless --manual flag is provided."""
    if config.getoption("--manual"):
        return

    skip_manual = pytest.mark.skip(reason="Manual test - run with --manual flag")
    for item in items:
        if "manual" in item.keywords:
            item.add_marker(skip_manual)


@pytest.fixture(scope="function", autouse=True)
def reset_job_status():
    from laws.models import JobStatus

    JobStatus.objects.all().delete()


@pytest.fixture(scope="function", autouse=True)
def cleanup_cost_rows(request, django_db_blocker):
    from django.db.transaction import TransactionManagementError

    from otto.models import Cost

    uses_database = request.node.get_closest_marker("django_db") is not None or any(
        fixture_name in request.fixturenames
        for fixture_name in [
            "db",
            "transactional_db",
            "django_db_reset_sequences",
            "django_db_serialized_rollback",
            "live_server",
        ]
    )

    yield

    if not uses_database:
        return

    # Some tests create Cost rows from background threads / async helpers that can
    # outlive the usual per-test transaction rollback. Clean them up explicitly so
    # later dashboard and aggregation tests don't inherit hidden global state.
    with django_db_blocker.unblock():
        try:
            Cost.objects.all().delete()
        except TransactionManagementError:
            # A few tests intentionally leave the current transaction in an error
            # state while asserting integrity constraints. In that case, let the
            # normal rollback finish instead of masking the real test result.
            pass


def _make_pdf_bytes(page_count: int = 1) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest_asyncio.fixture
async def pdf_library_document(all_apps_user):
    """
    Yields (user, library, saved_file, document) for a private library
    containing a single 4-page PDF document.  Cleans up after the test.
    """
    from django.core.files.base import ContentFile

    from librarian.models import DataSource, Document, Library, SavedFile

    user = await sync_to_async(all_apps_user)()

    @sync_to_async
    def _create():
        library = Library.objects.create(
            name=f"Test Library {uuid.uuid4().hex[:8]}",
            created_by=user,
            is_public=False,
        )
        data_source = DataSource.objects.create(library=library, name="Test Folder")
        saved_file = SavedFile.objects.create(
            file=ContentFile(_make_pdf_bytes(page_count=4), name="test.pdf"),
            content_type="application/pdf",
        )
        document = Document.objects.create(
            data_source=data_source,
            saved_file=saved_file,
            filename="test.pdf",
            status="INDEXED",
        )
        return library, saved_file, document

    library, saved_file, document = await _create()
    yield user, library, saved_file, document

    @sync_to_async
    def _cleanup():
        Library.objects.filter(id=library.id).delete()

    await _cleanup()
