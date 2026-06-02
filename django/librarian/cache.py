from django.core.cache import cache


def _get_cache_key(document_id, key):
    return f"document_{document_id}_{key}"


def get_celery_task_id(document_id):
    return cache.get(_get_cache_key(document_id, "celery_task_id"))


def set_celery_task_id(document_id, task_id):
    cache.set(_get_cache_key(document_id, "celery_task_id"), task_id, timeout=None)


def get_azure_operation_location(document_id):
    return cache.get(_get_cache_key(document_id, "azure_operation_location"))


def set_azure_operation_location(document_id, location):
    cache.set(
        _get_cache_key(document_id, "azure_operation_location"), location, timeout=None
    )


def get_pending_embedding_chunks(document_id):
    return cache.get(_get_cache_key(document_id, "pending_embedding_chunks"))


def set_pending_embedding_chunks(document_id, chunks):
    cache.set(
        _get_cache_key(document_id, "pending_embedding_chunks"),
        chunks,
        timeout=None,
    )


def clear_pending_embedding_chunks(document_id):
    cache.delete(_get_cache_key(document_id, "pending_embedding_chunks"))


def clear_document_cache(document_id):
    cache.delete_many(
        [
            _get_cache_key(document_id, "celery_task_id"),
            _get_cache_key(document_id, "azure_operation_location"),
            _get_cache_key(document_id, "pending_embedding_chunks"),
        ]
    )


# Embedding progress checkpointing -------------------------------------------------
# Note: The progress tracking is now primarily handled by
# librarian.utils.batch_embedding.BatchEmbeddingProgress
# These functions remain for backward compatibility.


def get_embedding_progress(document_id):
    """Return embedding progress dict or None.

    Example structure:
    {
        "session_id": "<uuid>",
        "next_index": 0,           # child chunk index (excludes document node)
        "total": 123,              # total child chunks
        "delete_done": False,      # whether we deleted existing vectors for this session
        "last_update_ts": 1730000000.0,
    }
    """
    return cache.get(_get_cache_key(document_id, "embedding_progress"))


def set_embedding_progress(document_id, progress: dict):
    cache.set(_get_cache_key(document_id, "embedding_progress"), progress, timeout=None)


def clear_embedding_progress(document_id):
    cache.delete(_get_cache_key(document_id, "embedding_progress"))
