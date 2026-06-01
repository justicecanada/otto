import pytest

from librarian.cache import (
    get_celery_task_id,
    get_embedding_progress,
    set_celery_task_id,
)
from librarian.models import DataSource, Document


@pytest.mark.django_db
def test_finalize_document_light_blocks_after_repeated_non_advancing_requeues(
    all_apps_user, monkeypatch
):
    from librarian.tasks import finalize_document_light

    non_advancing_requeue_limit = 3

    user = all_apps_user()
    data_source = DataSource.objects.create(
        library=user.personal_library,
        name="File processing diagnostics",
    )
    document = Document.objects.create(
        data_source=data_source,
        filename="probe.txt",
        status="TEXT_EXTRACTED",
        url_content_type="text/plain",
    )
    set_celery_task_id(document.id, "initial-task")

    monkeypatch.setattr(
        "librarian.tasks.create_nodes",
        lambda chunks, document: ["document-node", "chunk-1", "chunk-2"],
    )
    monkeypatch.setattr("librarian.tasks.check_cancel", lambda *args, **kwargs: None)

    class DummyIndex:
        def delete_ref_doc(self, *args, **kwargs):
            return None

        def insert_nodes(self, *args, **kwargs):
            return None

    class DummyLLM:
        def __init__(self, *args, **kwargs):
            pass

        def get_index(self, library_uuid):
            return DummyIndex()

        def create_costs(self):
            return None

    monkeypatch.setattr("librarian.tasks.OttoLLM", DummyLLM)
    monkeypatch.setattr(
        "librarian.utils.batch_embedding.insert_nodes_with_checkpointing",
        lambda **kwargs: {"ok": True, "requeued": "requeued-task", "next_index": 0},
    )

    class DummySelf:
        backend = None

        def __init__(self, task_id):
            self.request = type(
                "Req",
                (),
                {"id": task_id, "delivery_info": {"priority": 6}},
            )()

    task_func = finalize_document_light.__wrapped__.__func__  # type: ignore[attr-defined]

    for idx in range(non_advancing_requeue_limit):
        result = task_func(
            DummySelf(f"task-{idx}"),
            document_id=document.id,
            chunks=["first chunk", "second chunk"],
            mock_embedding=True,
            start_index=0,
        )
        assert result["requeued"] == "requeued-task"
        document.refresh_from_db()
        assert document.status == "TEXT_EXTRACTED"

    result = task_func(
        DummySelf("task-final"),
        document_id=document.id,
        chunks=["first chunk", "second chunk"],
        mock_embedding=True,
        start_index=0,
    )

    document.refresh_from_db()

    assert result.get("error") == "stuck_progress"
    assert document.status == "BLOCKED"
    assert "without advancing past chunk" in (document.status_details or "")
    assert get_celery_task_id(document.id) is None
    assert get_embedding_progress(document.id) is None


@pytest.mark.django_db
def test_finalize_document_light_allows_progressing_requeues(
    all_apps_user, monkeypatch
):
    from librarian.tasks import finalize_document_light

    user = all_apps_user()
    data_source = DataSource.objects.create(
        library=user.personal_library,
        name="File processing diagnostics",
    )
    document = Document.objects.create(
        data_source=data_source,
        filename="probe.txt",
        status="TEXT_EXTRACTED",
        url_content_type="text/plain",
    )
    set_celery_task_id(document.id, "initial-task")

    monkeypatch.setattr(
        "librarian.tasks.create_nodes",
        lambda chunks, document: ["document-node", "chunk-1", "chunk-2", "chunk-3"],
    )
    monkeypatch.setattr("librarian.tasks.check_cancel", lambda *args, **kwargs: None)

    class DummyIndex:
        def delete_ref_doc(self, *args, **kwargs):
            return None

        def insert_nodes(self, *args, **kwargs):
            return None

    class DummyLLM:
        def __init__(self, *args, **kwargs):
            pass

        def get_index(self, library_uuid):
            return DummyIndex()

        def create_costs(self):
            return None

    monkeypatch.setattr("librarian.tasks.OttoLLM", DummyLLM)

    call_count = {"value": 0}

    def fake_insert_nodes_with_progress(**kwargs):
        progress_tracker = kwargs["progress_tracker"]
        progress = progress_tracker.get() or {}
        call_count["value"] += 1

        if call_count["value"] == 1:
            progress["next_index"] = 1
            progress_tracker.set(progress)
            return {"ok": True, "requeued": "requeued-task-1", "next_index": 1}

        if call_count["value"] == 2:
            progress["next_index"] = 2
            progress_tracker.set(progress)
            return {"ok": True, "requeued": "requeued-task-2", "next_index": 2}

        progress_tracker.clear()
        return {"ok": True, "next_index": len(kwargs["nodes"])}

    monkeypatch.setattr(
        "librarian.utils.batch_embedding.insert_nodes_with_checkpointing",
        fake_insert_nodes_with_progress,
    )

    class DummySelf:
        backend = None

        def __init__(self, task_id):
            self.request = type(
                "Req",
                (),
                {"id": task_id, "delivery_info": {"priority": 6}},
            )()

    task_func = finalize_document_light.__wrapped__.__func__  # type: ignore[attr-defined]

    result = task_func(
        DummySelf("task-0"),
        document_id=document.id,
        chunks=["first chunk", "second chunk", "third chunk"],
        mock_embedding=True,
        start_index=0,
    )
    assert result["requeued"] == "requeued-task-1"
    document.refresh_from_db()
    progress = get_embedding_progress(document.id)
    assert document.status == "TEXT_EXTRACTED"
    assert progress is not None
    assert progress["next_index"] == 1
    assert progress["stuck_counter"] == 0

    result = task_func(
        DummySelf("task-1"),
        document_id=document.id,
        chunks=["first chunk", "second chunk", "third chunk"],
        mock_embedding=True,
        start_index=1,
    )
    assert result["requeued"] == "requeued-task-2"
    document.refresh_from_db()
    progress = get_embedding_progress(document.id)
    assert document.status == "TEXT_EXTRACTED"
    assert progress is not None
    assert progress["next_index"] == 2
    assert progress["stuck_counter"] == 0

    result = task_func(
        DummySelf("task-final"),
        document_id=document.id,
        chunks=["first chunk", "second chunk", "third chunk"],
        mock_embedding=True,
        start_index=2,
    )

    document.refresh_from_db()

    assert result["status"] == "SUCCESS"
    assert document.status == "SUCCESS"
    assert get_celery_task_id(document.id) is None
    assert get_embedding_progress(document.id) is None
