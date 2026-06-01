import types

from django.db import IntegrityError

import pytest
from structlog.contextvars import bind_contextvars, get_contextvars, unbind_contextvars

from otto.priorities import LOW, MEDIUM

from librarian.cache import (
    get_celery_task_id,
    get_embedding_progress,
    set_celery_task_id,
)
from librarian.models import DataSource, Document
from librarian.utils.batch_embedding import (
    BatchEmbeddingProgress,
    insert_nodes_with_checkpointing,
)


class _RateLimitError(Exception):
    def __init__(self, retry_after_seconds: float):
        super().__init__(
            f"Embedding rate limited. Please retry after {retry_after_seconds} seconds."
        )
        self.response = types.SimpleNamespace(
            status_code=429,
            headers={"Retry-After": str(retry_after_seconds)},
        )


@pytest.mark.django_db
def test_insert_nodes_with_checkpointing_does_not_retry_or_requeue_integrity_errors(
    monkeypatch,
):
    progress_tracker = BatchEmbeddingProgress("test_embedding_integrity_error")
    progress_tracker.clear()
    sleep_calls = []
    requeue_calls = []

    monkeypatch.setattr(
        "librarian.utils.batch_embedding.time.sleep",
        lambda seconds: sleep_calls.append(seconds),
    )

    class BrokenIndex:
        def insert_nodes(self, batch_nodes):
            raise IntegrityError("fk violation")

    with pytest.raises(IntegrityError, match="fk violation"):
        insert_nodes_with_checkpointing(
            nodes=["chunk-1", "chunk-2"],
            vector_store_index=BrokenIndex(),
            progress_tracker=progress_tracker,
            requeue_fn=lambda next_index, countdown_seconds=0, requeue_reason=None: (
                requeue_calls.append(
                    {
                        "next_index": next_index,
                        "countdown_seconds": countdown_seconds,
                        "requeue_reason": requeue_reason,
                    }
                )
                or "task-123"
            ),
            batch_size=2,
            time_slice_seconds=1,
        )

    assert sleep_calls == []
    assert requeue_calls == []

    progress_tracker.clear()


@pytest.mark.django_db
def test_insert_nodes_with_checkpointing_requeues_with_backoff_countdown_when_retry_after_exceeds_remaining_slice(
    monkeypatch,
):
    cache_key = "test_embedding_requeue_retry_after"
    progress_tracker = BatchEmbeddingProgress(cache_key)
    progress_tracker.clear()

    monotonic_values = iter([100.0, 100.5])
    sleep_calls = []
    status_updates = []
    requeue_calls = []

    monkeypatch.setattr(
        "librarian.utils.batch_embedding.time.monotonic",
        lambda: next(monotonic_values, 100.5),
    )
    monkeypatch.setattr(
        "librarian.utils.batch_embedding.time.sleep",
        lambda seconds: sleep_calls.append(seconds),
    )
    monkeypatch.setattr(
        "librarian.utils.batch_embedding.random.uniform",
        lambda _a, _b: 0.0,
    )

    class AlwaysRateLimitedIndex:
        def insert_nodes(self, batch_nodes):
            raise _RateLimitError(retry_after_seconds=2)

    result = insert_nodes_with_checkpointing(
        nodes=["chunk-1", "chunk-2"],
        vector_store_index=AlwaysRateLimitedIndex(),
        progress_tracker=progress_tracker,
        update_status_fn=status_updates.append,
        requeue_fn=lambda next_index, countdown_seconds=0, requeue_reason=None: (
            requeue_calls.append(
                {
                    "next_index": next_index,
                    "countdown_seconds": countdown_seconds,
                    "requeue_reason": requeue_reason,
                }
            )
            or "task-123"
        ),
        batch_size=2,
        time_slice_seconds=1,
    )

    progress = progress_tracker.get()

    assert result == {"ok": True, "requeued": "task-123", "next_index": 0}
    assert requeue_calls == [
        {
            "next_index": 0,
            "countdown_seconds": 2.0,
            "requeue_reason": "retry_backoff",
        }
    ]
    assert sleep_calls == []
    assert status_updates[-1] == "Adding to library... (0/2 - waiting)"
    assert progress is not None
    assert progress["next_index"] == 0
    assert progress["initial_status_text"] == "Adding to library... (0/2 - waiting)"
    assert progress["requeue_reason"] == "retry_backoff"
    assert progress["requeue_countdown_seconds"] == 2.0

    progress_tracker.clear()


@pytest.mark.django_db
def test_finalize_document_light_requeue_continuation_uses_countdown_and_preserves_priority(
    all_apps_user, monkeypatch
):
    from librarian.tasks import finalize_document_light

    user = all_apps_user()
    data_source = DataSource.objects.create(
        library=user.personal_library,
        name="Embedding requeue integration",
    )
    document = Document.objects.create(
        data_source=data_source,
        filename="large-document.txt",
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

    apply_async_calls = []

    class DummyResult:
        id = "requeued-task"
        backend = None

    def fake_apply_async(*args, **kwargs):
        apply_async_calls.append(kwargs)
        return DummyResult()

    monkeypatch.setattr(finalize_document_light, "apply_async", fake_apply_async)

    def fake_insert_nodes_with_checkpointing(**kwargs):
        task_id = kwargs["requeue_fn"](
            kwargs["start_index"],
            countdown_seconds=12.5,
            requeue_reason="retry_backoff",
        )
        return {"ok": True, "requeued": task_id, "next_index": kwargs["start_index"]}

    monkeypatch.setattr(
        "librarian.utils.batch_embedding.insert_nodes_with_checkpointing",
        fake_insert_nodes_with_checkpointing,
    )

    class DummySelf:
        backend = None

        def __init__(self, task_id, priority):
            self.request = type(
                "Req",
                (),
                {"id": task_id, "delivery_info": {"priority": priority}},
            )()

    task_func = finalize_document_light.__wrapped__.__func__  # type: ignore[attr-defined]

    result = task_func(
        DummySelf("task-0", LOW),
        document_id=document.id,
        chunks=["first chunk", "second chunk"],
        mock_embedding=True,
        start_index=0,
    )

    document.refresh_from_db()
    progress = get_embedding_progress(document.id)

    assert result == {"ok": True, "requeued": "requeued-task", "next_index": 0}
    assert len(apply_async_calls) == 1
    assert apply_async_calls[0]["priority"] == LOW
    assert apply_async_calls[0]["countdown"] == 12.5
    assert apply_async_calls[0]["kwargs"]["start_index"] == 0
    assert document.status == "TEXT_EXTRACTED"
    assert get_celery_task_id(document.id) == "requeued-task"
    assert progress is not None
    assert progress["next_index"] == 0


@pytest.mark.django_db
def test_requeued_large_document_continuations_do_not_escalate_priority(
    all_apps_user, monkeypatch
):
    from librarian.tasks import finalize_document_light

    user = all_apps_user()
    data_source = DataSource.objects.create(
        library=user.personal_library,
        name="Embedding priority escalation",
    )
    document = Document.objects.create(
        data_source=data_source,
        filename="priority-escalation.txt",
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

    apply_async_calls = []

    class DummyResult:
        backend = None

        def __init__(self, task_id):
            self.id = task_id

    def fake_apply_async(*args, **kwargs):
        task_id = f"requeued-task-{len(apply_async_calls)}"
        apply_async_calls.append({**kwargs, "task_id": task_id})
        return DummyResult(task_id)

    monkeypatch.setattr(finalize_document_light, "apply_async", fake_apply_async)

    def fake_insert_nodes_with_checkpointing(**kwargs):
        task_id = kwargs["requeue_fn"](kwargs["start_index"])
        return {"ok": True, "requeued": task_id, "next_index": kwargs["start_index"]}

    monkeypatch.setattr(
        "librarian.utils.batch_embedding.insert_nodes_with_checkpointing",
        fake_insert_nodes_with_checkpointing,
    )

    class DummySelf:
        backend = None

        def __init__(self, task_id, priority):
            self.request = type(
                "Req",
                (),
                {"id": task_id, "delivery_info": {"priority": priority}},
            )()

    task_func = finalize_document_light.__wrapped__.__func__  # type: ignore[attr-defined]

    first_result = task_func(
        DummySelf("task-low", LOW),
        document_id=document.id,
        chunks=["first chunk", "second chunk"],
        mock_embedding=True,
        start_index=0,
    )
    second_result = task_func(
        DummySelf("task-medium", MEDIUM),
        document_id=document.id,
        chunks=["first chunk", "second chunk"],
        mock_embedding=True,
        start_index=0,
    )

    assert first_result["requeued"] == "requeued-task-0"
    assert second_result["requeued"] == "requeued-task-1"
    assert [call["priority"] for call in apply_async_calls] == [LOW, MEDIUM]
    assert all("countdown" not in call for call in apply_async_calls)


@pytest.mark.django_db
def test_finalize_document_light_clears_stale_message_context_before_creating_costs(
    all_apps_user, monkeypatch
):
    from librarian.tasks import finalize_document_light

    user = all_apps_user()
    data_source = DataSource.objects.create(
        library=user.personal_library,
        name="Embedding context cleanup",
    )
    document = Document.objects.create(
        data_source=data_source,
        filename="context-cleanup.txt",
        status="TEXT_EXTRACTED",
        url_content_type="text/plain",
    )

    monkeypatch.setattr(
        "librarian.tasks.create_nodes",
        lambda chunks, document: ["document-node", "chunk-1"],
    )
    monkeypatch.setattr("librarian.tasks.check_cancel", lambda *args, **kwargs: None)

    class DummyIndex:
        def delete_ref_doc(self, *args, **kwargs):
            return None

        def insert_nodes(self, *args, **kwargs):
            return None

    class DummyLLM:
        def __init__(self, *args, **kwargs):
            self.create_costs_calls = 0

        def get_index(self, library_uuid):
            return DummyIndex()

        def create_costs(self):
            self.create_costs_calls += 1
            context = get_contextvars()
            assert context.get("feature") == "librarian"
            assert context.get("document_id") == document.id
            assert "message_id" not in context
            assert "message_next_id" not in context
            assert "law_id" not in context

    monkeypatch.setattr("librarian.tasks.OttoLLM", DummyLLM)

    class DummySelf:
        backend = None

        def __init__(self, task_id):
            self.request = type(
                "Req",
                (),
                {"id": task_id, "delivery_info": {"priority": LOW}},
            )()

    task_func = finalize_document_light.__wrapped__.__func__  # type: ignore[attr-defined]

    bind_contextvars(message_id=999, message_next_id=888, law_id=777)
    try:
        result = task_func(
            DummySelf("task-context-cleanup"),
            document_id=document.id,
            chunks=["first chunk"],
            mock_embedding=True,
            start_index=0,
        )
    finally:
        unbind_contextvars("message_id", "message_next_id", "law_id")

    document.refresh_from_db()

    assert result == {"document_id": document.id, "status": "SUCCESS"}
    assert document.status == "SUCCESS"
