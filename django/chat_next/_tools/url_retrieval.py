import asyncio
import time

from django.utils.translation import gettext as _

from asgiref.sync import sync_to_async
from structlog import get_logger
from structlog.contextvars import get_contextvars

from otto.utils.common import check_url_allowed

from librarian.models import DataSource, Document
from librarian.utils.library_management import ensure_chat_data_source

from chat_next._tools.base import TOOL_REGISTRY, OttoTool, ToolContext
from chat_next.message_attachments import ensure_message_attachment_for_document
from chat_next.models import TOOL_CATEGORY_URL_RETRIEVAL, Message
from chat_next.utils import bad_url

logger = get_logger(__name__)

DEFAULT_SUMMARY_TEMPLATE = _("Added %(count)s document(s) from %(url)s to %(folder)s.")
CHAT_FILE_WAIT_SECONDS = 60
CHAT_FILE_POLL_INTERVAL_SECONDS = 1.0


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    candidate = url.strip()
    if candidate.startswith("http://"):
        candidate = f"https://{candidate[7:]}"
    elif not candidate.startswith("http"):
        candidate = f"https://{candidate}"
    return candidate


async def retrieve_url_content(arguments: dict, context: ToolContext) -> dict:
    user = context.user
    if not user or not user.is_authenticated:
        return {"error": "You must be signed in to fetch URLs."}
    if not context.chat:
        return {"error": "Chat context is required to fetch URLs into chat uploads."}

    url = _normalize_url(arguments.get("url"))
    if not url:
        return {"error": "Provide a valid HTTPS URL."}

    allowed = await sync_to_async(check_url_allowed, thread_sensitive=True)(url)
    if not allowed:
        return {"error": bad_url(render_markdown=True)}

    selector = (arguments.get("selector") or "").strip() or None
    data_source = await sync_to_async(ensure_chat_data_source, thread_sensitive=True)(
        context.chat
    )
    library = await sync_to_async(lambda: data_source.library, thread_sensitive=True)()

    destination = {
        "library_id": library.id,
        "library_name": str(library),
        "data_source_id": data_source.id,
        "data_source_name": data_source.name,
    }

    documents_info, message_id = await _ingest_documents(
        destination["data_source_id"],
        url,
        selector,
    )

    if message_id:
        _schedule_chat_file_creation(documents_info, message_id)

    result: dict = {
        "url": url,
        "selector": selector,
        "documents": documents_info,
        "library": {
            "id": destination["library_id"],
            "name": destination["library_name"],
            "data_source_id": destination["data_source_id"],
            "data_source_name": destination["data_source_name"],
        },
    }

    summary_text = DEFAULT_SUMMARY_TEMPLATE % {
        "count": len(documents_info),
        "url": url,
        "folder": destination["data_source_name"],
    }
    result["message"] = summary_text
    result["NEXT_STEP"] = (
        "Documents are processing in the background. "
        "You can pass the document_id(s) directly to prompt_documents — "
        "it will wait for text extraction to complete before processing."
    )

    has_responses_client = bool(context.extra.get("responses_client"))
    if not has_responses_client:
        doc_ids = ", ".join(
            str(doc.get("document_id"))
            for doc in documents_info
            if doc.get("document_id")
        )
        if doc_ids:
            summary_text += _(" (document_id(s): %(doc_ids)s)") % {"doc_ids": doc_ids}
        return summary_text

    return result


async def _ingest_documents(
    data_source_id: int,
    url: str,
    selector: str | None,
) -> tuple[list[dict], int | None]:
    return await sync_to_async(
        _ingest_documents_sync,
        thread_sensitive=True,
    )(
        data_source_id,
        url,
        selector,
    )


def _ingest_documents_sync(
    data_source_id: int,
    url: str,
    selector: str | None,
) -> tuple[list[dict], int | None]:
    try:
        data_source = DataSource.objects.select_related("library").get(
            id=data_source_id
        )
    except DataSource.DoesNotExist:  # pragma: no cover - protected upstream
        raise ValueError("Data source not found")

    ctx = get_contextvars()
    message_id = ctx.get("message_next_id")
    message = None
    if message_id:
        try:
            message = Message.objects.get(id=message_id)
        except Message.DoesNotExist:
            message = None

    documents = []
    normalized_selector = selector or None
    doc_qs = Document.objects.filter(
        data_source=data_source,
        url=url,
    )
    if normalized_selector is None:
        doc_qs = doc_qs.filter(selector__isnull=True)
    else:
        doc_qs = doc_qs.filter(selector=normalized_selector)
    doc = doc_qs.first()
    if not doc:
        doc = Document.objects.create(
            data_source=data_source,
            url=url,
            selector=normalized_selector,
            provenance=Document.PROVENANCE_URL_RETRIEVAL,
        )
    elif doc.provenance == Document.PROVENANCE_UNKNOWN:
        doc.provenance = Document.PROVENANCE_URL_RETRIEVAL
        doc.save(update_fields=["provenance"])

    if message:
        doc.chat_next_messages.add(message)

    # Kick off processing (retries if prior error)
    try:
        doc.process()
    except Exception as exc:
        logger.warning(
            "Document processing failed to enqueue", document_id=doc.id, error=str(exc)
        )

    documents.append(
        {
            "document_id": doc.id,
            "status": doc.status,
            "url": doc.url,
            "filename": doc.filename,
            "library_id": data_source.library_id,
            "data_source_id": data_source.id,
        }
    )
    return documents, (message.id if message else None)


def _schedule_chat_file_creation(documents_info: list[dict], message_id: int) -> None:
    if not documents_info:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    # sync ingested chat-library documents into message attachments
    loop.create_task(_await_and_create_chat_files(documents_info, message_id))


async def _await_and_create_chat_files(
    documents_info: list[dict],
    message_id: int,
) -> None:
    tasks = []
    for doc_info in documents_info:
        doc_id = doc_info.get("document_id")
        if doc_id:
            tasks.append(_wait_for_document_and_create_chat_file(doc_id, message_id))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _wait_for_document_and_create_chat_file(doc_id: int, message_id: int) -> None:
    deadline = time.monotonic() + CHAT_FILE_WAIT_SECONDS
    while time.monotonic() < deadline:
        doc = await sync_to_async(
            _get_document_with_saved_file,
            thread_sensitive=True,
        )(doc_id)
        if doc and doc.saved_file_id:
            await sync_to_async(
                _create_chat_file_sync,
                thread_sensitive=True,
            )(doc_id, message_id)
            return
        await asyncio.sleep(CHAT_FILE_POLL_INTERVAL_SECONDS)


def _get_document_with_saved_file(doc_id: int) -> Document | None:
    try:
        return Document.objects.select_related("saved_file").get(id=doc_id)
    except Document.DoesNotExist:
        return None


def _create_chat_file_sync(doc_id: int, message_id: int) -> None:
    try:
        doc = Document.objects.select_related("saved_file").get(id=doc_id)
        message = Message.objects.get(id=message_id)
    except (Document.DoesNotExist, Message.DoesNotExist):
        return
    ensure_message_attachment_for_document(message, doc)


TOOL_REGISTRY.register(
    OttoTool(
        name="retrieve_url_content",
        description=(
            "Ingest an allow-listed URL into this chat's uploads library."
            # "Optionally provide a CSS selector to focus on part of the page or specific content."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "HTTPS URL to fetch."},
                "selector": {
                    "type": ["string", "null"],
                    "description": "Optional CSS selector to ingest only certain portions of the content.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        execute=retrieve_url_content,
        category=TOOL_CATEGORY_URL_RETRIEVAL,
        requires_user=True,
        requires_chat=True,
        strict=False,
        permission_check=lambda user, chat: user is not None and user.is_authenticated,
    )
)
