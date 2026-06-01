import os
import uuid
from contextlib import contextmanager

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import SynchronousOnlyOperation
from django.core.files.base import ContentFile

from azure.ai.translation.document import (
    DocumentTranslationClient,
)
from azure.core.credentials import AzureKeyCredential
from celery import current_task, shared_task
from structlog import get_logger

from otto.models import Cost
from otto.secure_models import AccessKey

from .models import InputFile, OutputFile
from .utils import (
    build_source_blob_name,
    build_target_blob_name,
)

logger = get_logger(__name__)


def _build_translation_blob_url(blob_path):
    return (
        f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net/"
        f"{settings.AZURE_CONTAINER}/{blob_path}"
    )


def _delete_translation_blob(blob_path):
    if not blob_path:
        return

    azure_storage = settings.AZURE_STORAGE
    try:
        azure_storage.delete(blob_path)
        azure_storage.delete(blob_path.rsplit("/", 1)[0])
    except Exception:
        logger.warning("Error deleting translation blob", blob_path=blob_path)


def _normalize_character_usage(raw_usage):
    """Normalize Azure usage to absolute character count (int).

    Some SDK responses can report usage as fractional million-character units.
    If usage is between 0 and 1, treat it as millions and scale to characters.
    """
    if raw_usage is None:
        return 0

    usage = float(raw_usage)
    if usage <= 0:
        return 0
    if usage <= 1:
        return int(round(usage * 1_000_000))
    return int(round(usage))


@contextmanager
def _allow_async_unsafe():
    """Temporarily disable Django async safety guard for known sync worker paths."""
    env_key = "DJANGO_ALLOW_ASYNC_UNSAFE"
    original = os.environ.get(env_key)
    os.environ[env_key] = "true"
    try:
        yield
    finally:
        if original is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = original


def _get_real_thread_class():
    """Return an OS-thread class even when gevent monkey patching is active."""
    try:
        from gevent import monkey as gevent_monkey

        return gevent_monkey.get_original("threading", "Thread")
    except Exception:
        import threading

        return threading.Thread


def _run_db_operation(func, *args, **kwargs):
    """Run a DB operation, retrying in a dedicated thread if async-context guarded.

    Django raises SynchronousOnlyOperation when ORM code runs while an asyncio
    loop is active on the current thread. Celery gevent workers can occasionally
    surface this; retrying the ORM call in a separate thread avoids that context.
    """
    try:
        return func(*args, **kwargs)
    except SynchronousOnlyOperation:
        # First retry in the same execution context with async guard temporarily disabled.
        try:
            with _allow_async_unsafe():
                return func(*args, **kwargs)
        except SynchronousOnlyOperation:
            # Final retry in a real OS thread with async guard disabled.
            result: dict[str, object] = {}
            error: dict[str, Exception] = {}

            def _runner():
                try:
                    with _allow_async_unsafe():
                        result["value"] = func(*args, **kwargs)
                except Exception as exc:  # pragma: no cover - propagated to caller
                    error["value"] = exc

            thread_class = _get_real_thread_class()
            thread = thread_class(target=_runner, daemon=True)
            thread.start()
            thread.join()

            if "value" in error:
                raise error["value"]
            return result.get("value")


@shared_task(soft_time_limit=600, queue=settings.LIGHT_QUEUE)
def translate_document_task(
    input_file_id,
    output_file_id,
    user_id,
    target_lang,
    cost_group_id=None,
    custom_translator_id=None,
):
    from structlog.contextvars import bind_contextvars

    # IDLING
    if current_task:
        current_task.update_state(state="IDLING")

    bind_contextvars(feature="translate", user_id=user_id, cost_group_id=cost_group_id)

    logger.info(
        "Translate task started",
        output_file_id=output_file_id,
        input_file_id=input_file_id,
        target_lang=target_lang,
        cost_group_id=cost_group_id,
    )

    User = get_user_model()
    user = _run_db_operation(
        User.objects.get, id=user_id
    )  # User.objects.get(id=user_id)
    access_key = AccessKey(user=user)
    output_file = OutputFile.objects.get(access_key=access_key, id=output_file_id)
    source_blob_path = None
    target_blob_path = None

    try:
        # PARSING
        input_file = InputFile.objects.get(access_key=access_key, id=input_file_id)
        with input_file.file.open("rb") as f:
            file_bytes = f.read()

        original_filename = input_file.original_filename
        source_lang = input_file.user_request.source_lang
        azure_target_lang = "fr-ca" if target_lang == "fr" else target_lang

        source_blob_name = build_source_blob_name(
            original_filename, source_lang, file_bytes
        )
        target_blob_name = build_target_blob_name(
            original_filename, target_lang, file_bytes
        )
        translation_run_id = uuid.uuid4()
        source_blob_path = (
            f"{settings.AZURE_STORAGE_TRANSLATION_INPUT_URL_SEGMENT}/"
            f"{translation_run_id}/{source_blob_name}"
        )
        target_blob_path = (
            f"{settings.AZURE_STORAGE_TRANSLATION_OUTPUT_URL_SEGMENT}/"
            f"{translation_run_id}/{target_blob_name}"
        )

        azure_storage = settings.AZURE_STORAGE

        # UPLOADING
        if current_task:
            current_task.update_state(state="UPLOADING")

        azure_storage.save(source_blob_path, ContentFile(file_bytes))

        # TRANSLATING
        if current_task:
            current_task.update_state(state="TRANSLATING")

        doc_client = DocumentTranslationClient(
            settings.AZURE_AI_SERVICES_ENDPOINT,
            AzureKeyCredential(settings.AZURE_AI_SERVICES_KEY),
        )
        poller = doc_client.begin_translation(
            _build_translation_blob_url(source_blob_path),
            _build_translation_blob_url(target_blob_path),
            azure_target_lang,
            storage_type="File",
            category_id=custom_translator_id,
        )
        results = list(poller.result())
        for doc in results:
            if doc.status != "Succeeded":
                raise Exception(
                    doc.error.message if doc.error else "Translation failed"
                )

        raw_poller_usage = (
            getattr(getattr(poller, "details", None), "total_characters_charged", 0)
            or 0
        )
        usage = _normalize_character_usage(raw_poller_usage)
        if usage <= 0:
            usage = sum(
                _normalize_character_usage(
                    getattr(doc, "characters_charged", None)
                    or getattr(doc, "translated_characters", None)
                    or 0
                )
                for doc in results
            )

        logger.info(
            "Translate usage evaluated",
            output_file_id=output_file_id,
            raw_poller_usage=raw_poller_usage,
            normalized_usage=usage,
            result_count=len(results),
        )

        cost_type = "translate-custom" if custom_translator_id else "translate-file"
        if usage > 0:
            cost = Cost.objects.new(cost_type=cost_type, count=usage)
            output_file.usd_cost = float(cost.usd_cost)
            logger.info(
                "Translate cost recorded",
                output_file_id=output_file_id,
                usage_characters=usage,
                cost_type=cost_type,
                usd_cost=float(cost.usd_cost),
            )
        else:
            logger.warning(
                "Translate usage is zero; skipping cost record",
                output_file_id=output_file_id,
                cost_type=cost_type,
            )

        # Download translated blob and save to Django storage
        with azure_storage.open(target_blob_path) as translated_file:
            translated_bytes = translated_file.read()
        name_no_ext, _, ext = original_filename.rpartition(".")
        output_filename = (
            f"{name_no_ext}_{target_lang}.{ext}"
            if ext
            else f"{original_filename}_{target_lang}"
        )

        output_file.file.save(
            output_filename, ContentFile(translated_bytes), save=False
        )
        output_file.file_name = output_filename
        output_file.celery_task_ids = []
        output_file.save(access_key=access_key)

        if current_task:
            current_task.update_state(state="FINISHED")

        logger.info(
            "Translate task completed",
            output_file_id=output_file_id,
            output_filename=output_filename,
            usd_cost=float(output_file.usd_cost),
        )

    except Exception as e:
        if current_task:
            current_task.update_state(state="FAILURE")
        logger.exception("Document translation failed", output_file_id=output_file_id)
        output_file.error_message = str(e)
        output_file.save(access_key=access_key)
        raise
    finally:
        _delete_translation_blob(source_blob_path)
        _delete_translation_blob(target_blob_path)
