"""Tests for HNSW automatic indexing functionality."""

from types import SimpleNamespace

from django.conf import settings

import pytest

from librarian.models import Library
from librarian.tasks import build_hnsw_index, delete_hnsw_index


class DummyResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class SequencedConnection:
    """Simple connection that returns predefined rows for queries."""

    def __init__(self, responses_by_query):
        self.responses_by_query = responses_by_query
        self.calls = []
        self.drop_called = False
        self.commit_called = False

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append(sql)
        for key, value in self.responses_by_query.items():
            if key in sql:
                result = value(sql, params)
                if isinstance(result, Exception):
                    raise result
                if result == "DROP":
                    self.drop_called = True
                    return DummyResult(None)
                return DummyResult(result)
        pytest.fail(f"Unexpected SQL: {sql}")

    def commit(self):
        self.commit_called = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeUrl:
    def __init__(self):
        self.password = "secret"

    def __str__(self):
        return "postgresql://user:secret@localhost/db"


class FakeEngine:
    def __init__(self, connect_conn, autocommit_conn=None):
        self._connect_conn = connect_conn
        self._autocommit_conn = autocommit_conn or connect_conn
        self.url = FakeUrl()

    def connect(self):
        return self._connect_conn

    def execution_options(self, **kwargs):
        return SimpleNamespace(connect=lambda: self._autocommit_conn)


@pytest.mark.django_db
def test_library_hnsw_defaults():
    """Test that new libraries have correct HNSW defaults."""
    library = Library.objects.create(name="Test Library")

    assert library.hnsw_enabled is None  # Automatic mode
    assert library.hnsw_status == "none"
    assert library.hnsw_task_id is None
    assert library.total_chunks == 0


@pytest.mark.django_db
def test_library_should_use_hnsw_automatic():
    """Test automatic HNSW decision based on threshold."""
    library = Library.objects.create(name="Test Library")

    # Below threshold - should not use HNSW
    library.total_chunks = settings.HNSW_THRESHOLD - 1
    library.save()
    assert library.should_use_hnsw() is False

    # At threshold - should use HNSW
    library.total_chunks = settings.HNSW_THRESHOLD
    library.save()
    assert library.should_use_hnsw() is True

    # Above threshold - should use HNSW
    library.total_chunks = settings.HNSW_THRESHOLD + 10000
    library.save()
    assert library.should_use_hnsw() is True


@pytest.mark.django_db
def test_library_should_use_hnsw_manual_override():
    """Test manual HNSW override settings."""
    library = Library.objects.create(name="Test Library")
    library.total_chunks = settings.HNSW_THRESHOLD - 1  # Below threshold
    library.save()

    # Manual enable - should use HNSW even below threshold
    library.hnsw_enabled = True
    library.save()
    assert library.should_use_hnsw() is True

    # Manual disable - should not use HNSW even above threshold
    library.total_chunks = settings.HNSW_THRESHOLD + 10000
    library.hnsw_enabled = False
    library.save()
    assert library.should_use_hnsw() is False

    # Back to automatic
    library.hnsw_enabled = None
    library.save()
    assert library.should_use_hnsw() is True  # Above threshold


@pytest.mark.django_db
def test_library_update_total_chunks(all_apps_user):
    """Test that update_total_chunks correctly calculates from documents."""
    from librarian.models import DataSource, Document

    user = all_apps_user()
    library = Library.objects.create(name="Test Library", created_by=user)
    data_source = DataSource.objects.create(
        name="Test Source",
        library=library,
    )

    # Create some documents
    Document.objects.create(
        data_source=data_source,
        num_chunks=100,
    )
    Document.objects.create(
        data_source=data_source,
        num_chunks=200,
    )
    Document.objects.create(
        data_source=data_source,
        num_chunks=50,
        is_container=True,  # Should be excluded
    )

    # Update total
    total = library.update_total_chunks()

    assert total == 300  # 100 + 200, excluding container
    assert library.total_chunks == 300


@pytest.mark.django_db
def test_library_reset_clears_hnsw_status():
    """Test that library.reset() clears HNSW status."""
    library = Library.objects.create(name="Test Library")
    library.hnsw_status = "ready"
    library.hnsw_task_id = "test-task-id"
    library.total_chunks = 50000
    library.save()

    library.reset(recreate=True)

    assert library.hnsw_status == "none"
    assert library.hnsw_task_id is None
    assert library.total_chunks == 0


@pytest.mark.django_db
def test_check_and_build_hnsw_triggers_when_threshold_crossed(monkeypatch, DummyResult):
    """When auto mode and threshold crossed, build should be queued."""
    import types

    library = Library.objects.create(name="Trigger Test")
    # Start below threshold in auto mode
    library.total_chunks = settings.HNSW_THRESHOLD - 1
    library.hnsw_enabled = None
    library.hnsw_status = "none"
    library.save()

    # Prepare stub for build_hnsw_index.delay
    called = {"uuid": None}

    def fake_delay(uuid_hex):
        called["uuid"] = uuid_hex
        return DummyResult()

    import librarian.tasks as ltasks

    monkeypatch.setattr(
        ltasks, "build_hnsw_index", types.SimpleNamespace(delay=fake_delay)
    )

    # Cross the threshold and trigger check
    library.total_chunks = settings.HNSW_THRESHOLD
    library.save(update_fields=["total_chunks"])
    library.check_and_build_hnsw()

    library.refresh_from_db()
    assert library.hnsw_status == "pending"
    assert library.hnsw_task_id == "fake-task-id"
    assert called["uuid"] == library.uuid_hex


@pytest.mark.django_db
def test_library_reset_safety_check(all_apps_user):
    """Test that library.reset() blocks when library has documents."""
    from librarian.models import DataSource, Document

    user = all_apps_user()
    library = Library.objects.create(name="Protected Library", created_by=user)
    data_source = DataSource.objects.create(
        name="Test Source",
        library=library,
    )
    # Add a document
    Document.objects.create(
        data_source=data_source,
        num_chunks=100,
    )

    # Attempt to reset should fail with ValueError
    import pytest

    with pytest.raises(ValueError, match="Cannot reset library"):
        library.reset()

    # But reset with force=True should work
    library.reset(force=True)

    # Verify it actually dropped the table
    assert library.hnsw_status == "none"
    assert library.total_chunks == 0


@pytest.mark.django_db
def test_use_hnsw_for_query():
    """Test that use_hnsw_for_query only returns True when index is ready."""
    library = Library.objects.create(name="Query Test")

    # Below threshold, auto mode, no index - should be False
    library.total_chunks = settings.HNSW_THRESHOLD - 1
    library.hnsw_enabled = None
    library.hnsw_status = "none"
    library.save()
    assert library.use_hnsw_for_query() is False

    # Above threshold, auto mode, but no index yet - should be False
    library.total_chunks = settings.HNSW_THRESHOLD
    library.hnsw_status = "none"
    library.save()
    assert library.use_hnsw_for_query() is False

    # Above threshold, auto mode, index building - should be False (not ready yet)
    library.hnsw_status = "building"
    library.save()
    assert library.use_hnsw_for_query() is False

    # Above threshold, auto mode, index ready - should be True
    library.hnsw_status = "ready"
    library.save()
    assert library.use_hnsw_for_query() is True

    # Enabled mode, index ready - should be True
    library.hnsw_enabled = True
    library.total_chunks = 100  # Even below threshold
    library.hnsw_status = "ready"
    library.save()
    assert library.use_hnsw_for_query() is True

    # Enabled mode, but index not ready - should be False
    library.hnsw_status = "pending"
    library.save()
    assert library.use_hnsw_for_query() is False

    # Disabled mode, even if index ready - should be False
    library.hnsw_enabled = False
    library.hnsw_status = "ready"
    library.save()
    assert library.use_hnsw_for_query() is False


@pytest.mark.django_db
def test_delete_hnsw_index_drops_existing_index(monkeypatch):
    """delete_hnsw_index should drop the index and reset status when it exists."""

    library = Library.objects.create(name="Delete Index Test")
    library.hnsw_status = "ready"
    library.hnsw_task_id = "task-123"
    library.save(update_fields=["hnsw_status", "hnsw_task_id"])

    class FakeResult:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class FakeConnection:
        def __init__(self):
            self.select_calls = 0
            self.drop_called = False
            self.commit_called = False

        def execute(self, statement, params=None):
            sql = str(statement)
            if "SELECT indexname" in sql:
                self.select_calls += 1
                return FakeResult(("existing_index",))
            if "DROP INDEX" in sql:
                self.drop_called = True
                return FakeResult(None)
            pytest.fail(f"Unexpected SQL: {sql}")

        def commit(self):
            self.commit_called = True

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeEngine:
        def __init__(self, connection):
            self._connection = connection

        def connect(self):
            return self._connection

    fake_conn = FakeConnection()
    fake_engine = FakeEngine(fake_conn)

    monkeypatch.setattr("chat.llm.get_pg_engines", lambda: (fake_engine, None))

    result = delete_hnsw_index(library.uuid_hex)

    library.refresh_from_db()
    assert library.hnsw_status == "none"
    assert library.hnsw_task_id is None

    assert fake_conn.select_calls == 1
    assert fake_conn.drop_called is True
    assert fake_conn.commit_called is True

    expected_index = f"data_{library.uuid_hex}_embedding_idx"
    assert result == {
        "status": "success",
        "library_uuid": library.uuid_hex,
        "index_name": expected_index,
    }


@pytest.mark.django_db
def test_delete_hnsw_index_missing_library():
    """Function should return error payload when library UUID is invalid."""

    missing_uuid = "deadbeef" * 4
    result = delete_hnsw_index(missing_uuid)
    assert result == {"status": "error", "message": "Library not found"}


@pytest.mark.django_db
def test_delete_hnsw_index_handles_exception(monkeypatch):
    """Unexpected errors should mark the library as error and bubble message."""

    library = Library.objects.create(name="Exception Test")
    library.hnsw_status = "ready"
    library.save(update_fields=["hnsw_status"])

    def fake_get_pg_engines():
        raise RuntimeError("boom")

    monkeypatch.setattr("chat.llm.get_pg_engines", fake_get_pg_engines)

    result = delete_hnsw_index(library.uuid_hex)

    library.refresh_from_db()
    assert library.hnsw_status == "error"
    assert result == {"status": "error", "message": "boom"}


@pytest.mark.django_db
def test_build_hnsw_index_errors_when_table_missing(monkeypatch):
    library = Library.objects.create(name="No Table")
    library.total_chunks = 10
    library.save(update_fields=["total_chunks"])

    conn = SequencedConnection({"FROM pg_tables": lambda sql, params: None})
    monkeypatch.setattr("chat.llm.get_pg_engines", lambda: (FakeEngine(conn), None))

    result = build_hnsw_index(library.uuid_hex)

    library.refresh_from_db()
    assert library.hnsw_status == "error"
    assert library.total_chunks == 0
    assert result["status"] == "error"
    assert "action_required" in result


@pytest.mark.django_db
def test_build_hnsw_index_skips_when_index_exists(monkeypatch):
    library = Library.objects.create(name="Already Indexed")
    responses = {
        "FROM pg_tables": lambda sql, params: ("data",),
        "FROM pg_indexes": lambda sql, params: ("existing",),
    }
    conn = SequencedConnection(responses)

    monkeypatch.setattr("chat.llm.get_pg_engines", lambda: (FakeEngine(conn), None))

    result = build_hnsw_index(library.uuid_hex)

    library.refresh_from_db()
    assert library.hnsw_status == "ready"
    assert result == {
        "status": "already_exists",
        "library_uuid": library.uuid_hex,
        "index_name": f"data_{library.uuid_hex}_embedding_idx",
    }


@pytest.mark.django_db
def test_build_hnsw_index_creates_index(monkeypatch):
    library = Library.objects.create(name="Build Index")
    library.total_chunks = 123
    library.save(update_fields=["total_chunks"])

    select_conn = SequencedConnection(
        {
            "FROM pg_tables": lambda sql, params: ("data",),
            "FROM pg_indexes": lambda sql, params: None,
        }
    )
    create_conn = SequencedConnection({"CREATE INDEX": lambda sql, params: None})

    monkeypatch.setattr(
        "chat.llm.get_pg_engines",
        lambda: (FakeEngine(select_conn, create_conn), None),
    )

    result = build_hnsw_index(library.uuid_hex)

    library.refresh_from_db()
    assert library.hnsw_status == "ready"
    assert create_conn.calls and "CREATE INDEX" in create_conn.calls[0]
    assert result == {
        "status": "success",
        "library_uuid": library.uuid_hex,
        "index_name": f"data_{library.uuid_hex}_embedding_idx",
        "total_chunks": 123,
    }


@pytest.mark.django_db
def test_build_hnsw_index_missing_library():
    missing_uuid = "cafebabe" * 4
    result = build_hnsw_index(missing_uuid)
    assert result == {"status": "error", "message": "Library not found"}


@pytest.mark.django_db
def test_build_hnsw_index_handles_exception(monkeypatch):
    library = Library.objects.create(name="Build Error")

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr("chat.llm.get_pg_engines", boom)

    result = build_hnsw_index(library.uuid_hex)

    library.refresh_from_db()
    assert library.hnsw_status == "error"
    assert result == {"status": "error", "message": "boom"}
