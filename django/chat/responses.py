import asyncio
import time
import uuid

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import HttpResponse, StreamingHttpResponse
from django.template.loader import render_to_string
from django.utils import translation
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from asgiref.sync import sync_to_async
from rules.contrib.views import objectgetter
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from otto.priorities import LOW, MEDIUM
from otto.utils.decorators import permission_required

from chat._views.qa_response import qa_response
from chat.llm import OttoLLM
from chat.models import Message
from chat.tasks import translate_file
from chat.utils import (
    SSE_KEEPALIVE_INTERVAL_SECONDS,
    build_stream_context,
    chat_to_history,
    combine_batch_generators,
    combine_response_replacers,
    create_batches,
    generate_cost_warning,
    get_request_route_label,
    htmx_stream,
    link_chat_files_to_library,
    num_tokens_from_string,
    stream_to_replacer,
    summarize_long_text,
    update_qa_library_for_chat_uploads,
)
from librarian.cache import get_celery_task_id
from librarian.models import Document

logger = get_logger(__name__)

batch_size = getattr(settings, "PER_DOC_BATCH_SIZE", 5)


def _get_bounded_int(query_dict, key, default, minimum, maximum):
    try:
        return min(maximum, max(minimum, int(query_dict.get(key, default))))
    except (TypeError, ValueError):
        return default


def _build_simulated_markdown_table_header(column_count):
    header = (
        "| " + " | ".join(f"Column {index + 1}" for index in range(column_count)) + " |"
    )
    separator = "| " + " | ".join("---" for _ in range(column_count)) + " |"
    return [header, separator]


def _build_simulated_markdown_table_row(row_number, column_count, cell_length):
    cells = []
    for column_index in range(column_count):
        prefix = f"R{row_number:03d}-C{column_index + 1:02d}-"
        repeated = (prefix * ((cell_length // len(prefix)) + 1))[:cell_length]
        cells.append(repeated)
    return "| " + " | ".join(cells) + " |"


def _build_simulated_markdown_table_text(row_count, column_count, cell_length):
    table_lines = [
        "## Simulated streaming markdown table",
        "",
        "This response intentionally grows into a very large markdown table to stress-test frontend rendering.",
        "",
        *_build_simulated_markdown_table_header(column_count),
    ]
    for row_number in range(1, row_count + 1):
        table_lines.append(
            _build_simulated_markdown_table_row(
                row_number,
                column_count,
                cell_length,
            )
        )
    return "\n".join(table_lines)


def _debug_slow_stream_response(chat, response_message, request, switch_mode=False):
    """Return a deterministic slow SSE response for local browser testing.

    This is intentionally admin-only and non-production. It can exercise both
    the fixed keepalive behavior and the legacy silent-gap behavior by toggling
    keepalive comment frames on or off.
    """

    delay_seconds = 12.0
    chunk_count = 1
    disable_keepalive = request.GET.get("simulate_disable_keepalive") == "1"

    try:
        delay_seconds = max(
            0.0, float(request.GET.get("simulate_chunk_delay", delay_seconds))
        )
    except (TypeError, ValueError):
        pass

    chunk_count = _get_bounded_int(
        request.GET,
        "simulate_chunk_count",
        default=chunk_count,
        minimum=1,
        maximum=40,
    )

    llm = OttoLLM()

    if request.GET.get("simulate_markdown_table") == "1":
        table_rows = _get_bounded_int(
            request.GET,
            "simulate_table_rows",
            default=240,
            minimum=10,
            maximum=1200,
        )
        table_columns = _get_bounded_int(
            request.GET,
            "simulate_table_columns",
            default=6,
            minimum=2,
            maximum=12,
        )
        table_cell_length = _get_bounded_int(
            request.GET,
            "simulate_table_cell_length",
            default=48,
            minimum=8,
            maximum=120,
        )
        token_chunk_size = _get_bounded_int(
            request.GET,
            "simulate_token_chunk_size",
            default=24,
            minimum=1,
            maximum=256,
        )
        table_text = _build_simulated_markdown_table_text(
            table_rows,
            table_columns,
            table_cell_length,
        )

        async def slow_token_stream():
            for start_index in range(0, len(table_text), token_chunk_size):
                if start_index > 0 and delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
                yield table_text[start_index : start_index + token_chunk_size]

        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                llm,
                response_generator=slow_token_stream(),
                switch_mode=switch_mode,
                keepalive_interval_seconds=(
                    0 if disable_keepalive else SSE_KEEPALIVE_INTERVAL_SECONDS
                ),
            ),
            content_type="text/event-stream",
        )

    async def slow_stream():
        base_text = request.GET.get(
            "simulate_text", "Simulated slow response complete."
        )
        response_text = ""
        for chunk_index in range(chunk_count):
            await asyncio.sleep(delay_seconds)
            chunk_text = (
                f"{base_text} (chunk {chunk_index + 1}/{chunk_count})"
                if chunk_count > 1
                else base_text
            )
            response_text = f"{response_text}\n\n{chunk_text}".strip()
            yield response_text

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            llm,
            response_replacer=slow_stream(),
            switch_mode=switch_mode,
            keepalive_interval_seconds=(
                0 if disable_keepalive else SSE_KEEPALIVE_INTERVAL_SECONDS
            ),
        ),
        content_type="text/event-stream",
    )


def _should_queue_document_processing(document: Document) -> bool:
    """Return True when summarize flow should (re)queue document processing.

    Prevents duplicate task fan-out for documents that are already complete,
    explicitly stopped, or actively processing with a live Celery task.
    """

    status = document.status

    if status == "SUCCESS":
        return False

    # Respect explicit user stop; don't auto-restart from summarize polling.
    if status == "BLOCKED":
        return False

    # Fresh/retryable states should queue immediately.
    if status in {"PENDING", "INIT", "ERROR"}:
        return True

    # In-flight states should only restart if task tracking is missing.
    if status in {"PROCESSING", "TEXT_EXTRACTED"}:
        return not bool(get_celery_task_id(document.id))

    # Conservative default for uncommon states.
    return True


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def otto_response(request, message_id=None, switch_mode=False):
    """
    Stream a response to the user's message. Uses LlamaIndex to manage chat history.
    """
    # Test hook: simulate SSE error for testing error handling UI
    # Usage: Add ?simulate_sse_error=1 to the SSE URL
    if request.GET.get("simulate_sse_error") == "1":
        raise Exception("Simulated SSE error for testing")

    response_message = Message.objects.get(id=message_id)
    skip_cost = request.GET.get("cost_approved", "false").lower() == "true"

    try:
        chat = response_message.chat
        mode = chat.options.mode

        # Admin-only non-production hook: return a deterministic slow stream so
        # browser tests can compare legacy silent gaps against the fixed
        # keepalive behavior.
        if (
            settings.CHAT_DEBUG_STREAM_TESTS_ENABLED
            and request.GET.get("simulate_slow_stream") == "1"
        ):
            return _debug_slow_stream_response(
                chat,
                response_message,
                request,
                switch_mode=switch_mode,
            )

        # For costing and logging. Contextvars are accessible anytime during the request
        # including in async functions (i.e. htmx_stream) and Celery tasks.
        user_id = request.user.id
        active_cost_group = request.user.get_active_cost_group(request)
        cost_group_id = active_cost_group.id if active_cost_group else None
        bind_contextvars(
            message_id=message_id,
            feature=mode,
            user_id=user_id,
            cost_group_id=cost_group_id,
        )
        logger.info(
            "legacy_sse_request_started",
            route=get_request_route_label(request),
            mode=mode,
            switch_mode=switch_mode,
        )

        if mode == "chat":
            return chat_response(
                chat,
                response_message,
                skip_cost,
                switch_mode=switch_mode,
                request=request,
            )
        if mode == "summarize":
            return summarize_response(
                chat, response_message, skip_cost, request=request
            )
        if mode == "translate":
            return translate_response(
                chat, response_message, skip_cost, request=request
            )
        if mode == "qa":
            return qa_response(
                chat,
                response_message,
                skip_cost,
                switch_mode=switch_mode,
                request=request,
            )
        else:
            return error_response(chat, response_message, _("Invalid mode."))
    except Exception as e:
        return error_response(chat, response_message, e)


def chat_response(
    chat,
    response_message,
    skip_cost,
    switch_mode=False,
    request=None,
):
    cost_response = generate_cost_warning(chat, response_message, skip_cost, request)
    if cost_response:
        return cost_response

    model = chat.options.chat_model
    temperature = chat.options.chat_temperature
    reasoning_effort = chat.options.chat_reasoning_effort
    verbosity = chat.options.chat_verbosity
    llm = OttoLLM(
        model, temperature, reasoning_effort=reasoning_effort, verbosity=verbosity
    )

    chat_history = chat_to_history(chat)

    tokens = num_tokens_from_string(
        " ".join(message.content or "" for message in chat_history)
    )
    if tokens > llm.max_input_tokens:
        # In this case, just return an error. No LLM costs are incurred.
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                llm,
                response_str=_(
                    "**Error:** The chat is too long for this AI model.\n\nYou can try: \n"
                    "1. Starting a new chat\n"
                    "2. Using summarize mode, which can handle longer texts\n"
                    "3. Using a different model\n"
                ),
                switch_mode=switch_mode,
            ),
            content_type="text/event-stream",
        )

    response_replacer = llm.chat_stream(chat_history)

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            llm,
            response_replacer=response_replacer,
            switch_mode=switch_mode,
            stream_context=build_stream_context(request, "chat"),
        ),
        content_type="text/event-stream",
    )


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def stop_response(request, message_id):
    """
    Stop the response to the user's message.
    """
    cache.set(f"stop_response_{message_id}", True, timeout=60)

    return HttpResponse(200)


def summarize_response(
    chat,
    response_message,
    skip_cost=False,
    request=None,
    summarize_prompt_override=None,
):
    """
    Summarize the user's input text (or URL) and stream the response.
    If the summarization technique does not support streaming, send final response only.
    """
    user_message = response_message.parent
    files = user_message.sorted_files if user_message is not None else []
    # Allow callers to provide a one-off summarize_prompt without mutating chat.options
    summarize_prompt = (
        summarize_prompt_override
        if summarize_prompt_override is not None
        else chat.options.summarize_prompt
    )
    model = chat.options.summarize_model
    reasoning_effort = chat.options.summarize_reasoning_effort
    verbosity = chat.options.summarize_verbosity

    llm = OttoLLM(
        model, temperature=0.5, reasoning_effort=reasoning_effort, verbosity=verbosity
    )
    error_str = ""

    # Check if the message is a URL (and not just plain text with files)
    url_validator = URLValidator()
    is_url = False
    if user_message.text and not files:
        try:
            url_validator(user_message.text)
            is_url = True
        except ValidationError:
            pass

    logger.info(
        "summarize_request_shape",
        input_kind=("files" if len(files) > 0 else "url" if is_url else "text"),
        file_count=len(files),
        input_text_chars=len(user_message.text or ""),
        summarize_model=model,
    )
    stream_context = build_stream_context(
        request,
        "summarize",
        input_kind=("files" if len(files) > 0 else "url" if is_url else "text"),
        file_count=len(files),
        processing_wait_ms=0,
        document_count=0,
        success_document_count=0,
        error_document_count=0,
    )

    if len(files) > 0 or is_url:
        # Update Q&A library settings to "Chat Uploads" (for files and URLs)
        # Get the accordion HTML for OOB swap
        accordion_html = update_qa_library_for_chat_uploads(chat)

        # Handle URL by creating/reusing a Document (supports PDFs, Word docs, etc.)
        if is_url:
            existing_document = Document.objects.filter(
                data_source=chat.data_source, url=user_message.text
            ).first()

            if not existing_document:
                document = Document.objects.create(
                    data_source=chat.data_source,
                    url=user_message.text,
                )
                document.messages.add(user_message)
            else:
                document = existing_document
                document.messages.add(user_message)

            if _should_queue_document_processing(document):
                document.process(priority=LOW)

        # Link files to library (reuses existing documents to avoid duplicates)
        if files:
            link_chat_files_to_library(
                files, user_message, chat.data_source, priority=LOW
            )

        cost_response = generate_cost_warning(
            chat, response_message, skip_cost, request
        )
        if cost_response:
            return cost_response

        # Build a mapping from Document.id to the ChatFile for this message
        files_map = {f.document_id: f for f in files if getattr(f, "document_id", None)}

        docs_to_process = [
            file.document
            for file in files
            if file.document and _should_queue_document_processing(file.document)
        ]
        for doc in docs_to_process:
            doc.process(
                pdf_method="default", priority=MEDIUM, finalization_priority=LOW
            )

        message_documents_qs = Document.objects.filter(messages=user_message)

        if message_documents_qs.exists():
            total_message_documents = message_documents_qs.count()
            stream_context["document_count"] = total_message_documents
            terminal_statuses = {"SUCCESS", "TEXT_EXTRACTED"}
            error_statuses = {"ERROR", "BLOCKED"}

            def fetch_message_documents():
                return list(
                    Document.objects.filter(messages=user_message).order_by(
                        "created_at", "id"
                    )
                )

            def get_title(doc):
                chat_file = files_map.get(doc.id)
                if chat_file:
                    return chat_file.display_path
                return getattr(doc, "file_path", None) or doc.name

            async def document_extraction_and_summarization_generator():
                yield _("Extracting text from files...")

                known_ids: set[int] = set()
                pending_ids: set[int] = set()
                completed_ids: set[int] = set()
                error_ids: set[int] = set()
                wait_started_at = None

                documents = await sync_to_async(fetch_message_documents)()
                if not documents:
                    yield _("No documents found to summarize.")
                    return

                for doc in documents:
                    known_ids.add(doc.id)
                    if doc.status in terminal_statuses:
                        completed_ids.add(doc.id)
                    elif doc.status in error_statuses:
                        error_ids.add(doc.id)
                    else:
                        pending_ids.add(doc.id)

                if not pending_ids:
                    yield _("Text extraction complete. Starting processing...")
                else:
                    wait_started_at = time.monotonic()
                    logger.info(
                        "summarize_document_wait_started",
                        total_document_count=len(documents),
                        pending_document_count=len(pending_ids),
                        ready_document_count=len(completed_ids),
                        error_document_count=len(error_ids),
                    )

                while pending_ids:
                    await asyncio.sleep(1)
                    for doc_id in list(pending_ids):
                        task_id = await sync_to_async(get_celery_task_id)(doc_id)
                        refreshed_doc = await sync_to_async(Document.objects.get)(
                            id=doc_id
                        )
                        if refreshed_doc.status in terminal_statuses:
                            pending_ids.remove(doc_id)
                            completed_ids.add(doc_id)
                        elif refreshed_doc.status in error_statuses:
                            pending_ids.remove(doc_id)
                            error_ids.add(doc_id)
                        elif not task_id:
                            pending_ids.remove(doc_id)
                            completed_ids.add(doc_id)

                    new_docs = await sync_to_async(
                        lambda: [
                            doc
                            for doc in fetch_message_documents()
                            if doc.id not in known_ids
                        ]
                    )()
                    for doc in new_docs:
                        known_ids.add(doc.id)
                        if doc.status in terminal_statuses:
                            completed_ids.add(doc.id)
                        elif doc.status in error_statuses:
                            error_ids.add(doc.id)
                        else:
                            pending_ids.add(doc.id)

                    if pending_ids:
                        remaining_count = len(pending_ids)
                        completed_count = len(completed_ids) + len(error_ids)
                        total_count = remaining_count + completed_count
                        yield f"{_('Extracting text from files')} ({completed_count}/{total_count} {_('complete')})..."

                if wait_started_at is not None:
                    wait_ms = int((time.monotonic() - wait_started_at) * 1000)
                    stream_context["processing_wait_ms"] = wait_ms
                    logger.info(
                        "summarize_document_wait_finished",
                        total_document_count=len(known_ids),
                        ready_document_count=len(completed_ids),
                        error_document_count=len(error_ids),
                        wait_ms=wait_ms,
                    )

                yield _("Text extraction complete. Starting processing...")

                final_docs = await sync_to_async(fetch_message_documents)()
                success_docs = sorted(
                    [doc for doc in final_docs if doc.status in terminal_statuses],
                    key=lambda d: (
                        d.file_path or "",
                        d.filename or "",
                        d.created_at,
                        d.id,
                    ),
                )
                error_docs = [doc for doc in final_docs if doc.status in error_statuses]

                if not success_docs and not error_docs:
                    yield _("No documents were available to summarize.")
                    return

                titles: list[str] = []
                responses = []

                for doc in success_docs:
                    if await sync_to_async(cache.get)(
                        f"stop_response_{response_message.id}", False
                    ):
                        return
                    await sync_to_async(doc.refresh_from_db)()
                    text_to_summarize = doc.extracted_text

                    if text_to_summarize:
                        titles.append(get_title(doc))
                        responses.append(
                            summarize_long_text(
                                text_to_summarize,
                                llm,
                                summarize_prompt,
                            )
                        )
                    else:
                        error_id = str(uuid.uuid4())[:7]
                        error_str = _("Error: File has no text after extraction.")
                        # Translatable string extracted for xgettext compatibility
                        error_id_label = _("Error ID:")
                        error_str += f" _({error_id_label} {error_id})_"
                        responses.append(stream_to_replacer([error_str]))
                        titles.append(get_title(doc))
                        logger.error(
                            "Document has no text after extraction",
                            error_id=error_id,
                            message_id=response_message.id,
                            chat_id=chat.id,
                            document_id=doc.id,
                            status=doc.status,
                            status_details=doc.status_details,
                        )

                for doc in error_docs:
                    raw_error = doc.status_details or _(
                        "Error extracting text from file."
                    )
                    # Format Error ID as italic if present
                    import re

                    error_id_match = re.search(
                        r"\(Error ID:?\s*([a-f0-9]+)\)\s*$", raw_error
                    )
                    if error_id_match:
                        error_without_id = raw_error[: error_id_match.start()].strip()
                        error_id = error_id_match.group(1)
                        # Translatable string extracted for xgettext compatibility
                        error_id_label = _("Error ID:")
                        error_message = (
                            f"{error_without_id} _({error_id_label} {error_id})_"
                        )
                    else:
                        error_message = raw_error
                    title = get_title(doc)
                    titles.append(title)
                    responses.append(stream_to_replacer([error_message]))

                if not titles:
                    yield _("No documents were available to summarize.")
                    return

                title_batches = create_batches(titles, batch_size)
                response_batches = create_batches(responses, batch_size)
                batch_generators = [
                    combine_response_replacers(batch_responses, batch_titles)
                    for batch_responses, batch_titles in zip(
                        response_batches, title_batches
                    )
                ]
                total_extracted_chars = sum(
                    len(doc.extracted_text or "") for doc in success_docs
                )
                batch_count = len(batch_generators)
                stream_context["success_document_count"] = len(success_docs)
                stream_context["error_document_count"] = len(error_docs)
                logger.info(
                    "summarize_stream_batch_shape",
                    success_document_count=len(success_docs),
                    error_document_count=len(error_docs),
                    total_extracted_chars=total_extracted_chars,
                    batch_count=batch_count,
                    batch_size=batch_size,
                )

                async for response in combine_batch_generators(
                    batch_generators, total_count=len(titles)
                ):
                    yield response

            return StreamingHttpResponse(
                streaming_content=htmx_stream(
                    chat,
                    response_message.id,
                    llm,
                    response_replacer=document_extraction_and_summarization_generator(),
                    dots=True,
                    wrap_markdown=True,
                    remove_stop=False,
                    oob_html=accordion_html,
                    stream_context=stream_context,
                ),
                content_type="text/event-stream",
            )

        # Fallback: no message-linked documents (likely duplicates)
        titles = [file.display_path for file in files]
        responses = []
        for file in files:
            if cache.get(f"stop_response_{response_message.id}", False):
                break

            file.document.refresh_from_db()
            text_to_summarize = file.document.extracted_text

            if not text_to_summarize:
                error_id = str(uuid.uuid4())[:7]
                error_str = _(
                    "Error: File has no extracted text. Try re-uploading the file."
                )
                # Translatable string extracted for xgettext compatibility
                error_id_label = _("Error ID:")
                error_str += f" _({error_id_label} {error_id})_"
                responses.append(stream_to_replacer([error_str]))
                logger.error(
                    f"File {file.filename} has no text when no Celery tasks were needed",
                    error_id=error_id,
                    message_id=response_message.id,
                    chat_id=chat.id,
                )
                continue
            responses.append(
                summarize_long_text(
                    text_to_summarize,
                    llm,
                    summarize_prompt,
                )
            )
        title_batches = create_batches(titles, batch_size)
        response_batches = create_batches(responses, batch_size)
        batch_generators = [
            combine_response_replacers(
                batch_responses,
                batch_titles,
            )
            for batch_responses, batch_titles in zip(response_batches, title_batches)
        ]

        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                llm,
                response_replacer=combine_batch_generators(
                    batch_generators, total_count=len(titles)
                ),
                dots=len(batch_generators) > 1,
                oob_html=accordion_html,
                stream_context=stream_context,
            ),
            content_type="text/event-stream",
        )
    elif user_message.text == "":
        error_str = _("No text to summarize.")
    else:
        # Plain text input (not a URL or file)
        text_to_summarize = user_message.text

    if error_str:
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                llm,
                response_str=error_str,
                stream_context=stream_context,
            ),
            content_type="text/event-stream",
        )

    response = summarize_long_text(
        text_to_summarize,
        llm,
        summarize_prompt,
    )
    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            llm,
            response_replacer=response,
            stream_context=stream_context,
        ),
        content_type="text/event-stream",
    )


def translate_response(chat, response_message, skip_cost=False, request=None):
    """
    Translate the user's input text and stream the response.
    If the translation technique does not support streaming, send final response only.
    """
    from chat.utils import translate_text_with_azure

    # Capture the user's GUI language at request time for use in async context
    user_language = get_language() or "en"

    if "gpt" in chat.options.translate_model:
        llm = OttoLLM(chat.options.translate_model, temperature=0.1)
    else:
        llm = OttoLLM(temperature=0.1)
    user_message = response_message.parent
    files = user_message.sorted_files if user_message is not None else []
    language = chat.options.translate_language
    custom_translator_id = (
        settings.CUSTOM_TRANSLATOR_ID
        if chat.options.translate_model == "azure_custom"
        else None
    )
    translation_method = chat.options.translate_model
    target_language = {"en": "English", "fr": "French"}[language]

    # Check estimated cost for translation requests (text or files)
    # This ensures we warn users for expensive translations as we do for other modes.
    cost_response = generate_cost_warning(chat, response_message, skip_cost, request)
    if cost_response:
        return cost_response

    def file_msg(response_message, total_files):
        # Use translation.override to ensure correct language in async context
        with translation.override(user_language):
            return render_to_string(
                "chat/components/message_files.html",
                context={"message": response_message, "total_files": total_files},
            )

    async def file_translation_generator(task_ids):
        # Use translation.override for all translated strings in async context
        with translation.override(user_language):
            initiating_msg = _("Initiating translation...")
            translating_msg = _("Translating file")
            error_translating_msg = _("Error translating")
        yield f"<p>{initiating_msg}</p>"
        any_task_done = False
        try:
            failed_tasks = []
            while task_ids:
                # To prevent constantly checking the task status, we sleep for a bit
                # File translation is very slow so every few seconds is plenty.
                await asyncio.sleep(1)
                for task_id in task_ids.copy():
                    task = translate_file.AsyncResult(task_id)
                    # If the task is not running, remove it from the list
                    if task.state in ["SUCCESS", "FAILURE", "REVOKED", "TIMEOUT"]:
                        any_task_done = True
                        task_ids.remove(task_id)
                        # Collect failed task details
                        if task.state in ["FAILURE", "REVOKED", "TIMEOUT"]:
                            failed_tasks.append(
                                {
                                    "task_id": task_id,
                                    "state": task.state,
                                    "error": (
                                        str(task.result)
                                        if task.result
                                        else f"Task {task.state.lower()}"
                                    ),
                                }
                            )
                        # Refresh the response message from the database
                        await sync_to_async(response_message.refresh_from_db)()
                if not any_task_done:
                    yield f"<p>{translating_msg} 1/{len(files)}...</p>"

            # After all tasks complete, generate final file status content
            file_content = await sync_to_async(file_msg)(response_message, len(files))

            # Add error details if any tasks failed
            if failed_tasks:
                error_details = []
                for failed_task in failed_tasks:
                    error_id = str(uuid.uuid4())[:7]
                    error_msg = failed_task["error"]

                    # Clean up the error message - extract just the file path
                    # and replace English "Error translating" with translated version
                    if "Error translating" in error_msg:
                        # Extract the file path from the error message
                        file_path = error_msg.replace("Error translating", "").strip()
                        # Get just the filename from the path
                        filename = (
                            file_path.split("/")[-1] if "/" in file_path else file_path
                        )
                        error_msg = f"{error_translating_msg} {filename}"
                    elif "Translation failed:" in error_msg:
                        # Keep the original error details after "Translation failed:"
                        parts = error_msg.split("Translation failed:")
                        if len(parts) > 1:
                            error_msg = parts[1].strip()

                    # Translatable string extracted for xgettext compatibility
                    # Use translation.override for correct language in async context
                    with translation.override(user_language):
                        error_label = _("Error")
                    error_details.append(
                        f"<div class='alert alert-warning mt-2 mb-0'><strong>{error_label} {error_id}:</strong> {error_msg}</div>"
                    )

                    # Log with error ID for debugging
                    logger.error(
                        "Translation task failed",
                        error_id=error_id,
                        task_id=failed_task["task_id"],
                        task_state=failed_task["state"],
                        error_details=failed_task["error"],
                        message_id=response_message.id,
                        chat_id=chat.id,
                    )

                file_content += "".join(error_details)

            yield file_content
        except Exception as e:
            from otto.utils.common import generate_ai_error_summary

            error_id = str(uuid.uuid4())[:7]
            error_str = await sync_to_async(generate_ai_error_summary)(e, error_id)
            logger.exception(
                "Error in file translation generator",
                error_id=error_id,
                message_id=response_message.id,
                chat_id=chat.id,
            )
            # For unexpected errors, show file status with generic error
            file_content = await sync_to_async(file_msg)(response_message, len(files))
            yield (
                file_content + f"<div class='alert alert-danger mt-2'>{error_str}</div>"
            )

    if "gpt" in translation_method:
        translate_prompt = (
            "<document>\n{docs}\n</document>\n<instruction>\n"
            + chat.options.translate_prompt
            + f"\nTranslate the document above to Canadian {target_language}. Output the translated text only.\n"
            + "</instruction>"
        )
        # Use summarize mode for file translation, to reuse text extraction etc.
        # Do NOT mutate chat.options.summarize_prompt (it is persisted and can
        # be captured elsewhere such as feedback snapshots). Instead, pass the
        # translate_prompt as a one-off override to summarize_response.
        response = summarize_response(
            chat,
            response_message,
            skip_cost,
            request=request,
            summarize_prompt_override=translate_prompt,
        )
        return response

    elif "azure" in translation_method and len(files) > 0:
        # Link files to library (reuses existing documents to avoid duplicates)
        link_chat_files_to_library(files, user_message, chat.data_source, priority=LOW)

        cost_response = generate_cost_warning(
            chat, response_message, skip_cost, request
        )
        if cost_response:
            return cost_response

        # Initiate the Celery task for translating each file with Azure
        task_ids = []
        glossary_path = (
            chat.options.translate_glossary.file.path
            if chat.options.translate_glossary
            else None
        )
        for file in files:
            # file is a django ChatFile object with property "file" that is a FileField
            # We need the path of the file to pass to the Celery task
            file_path = file.saved_file.file.path
            # Use custom translator if selected
            task = translate_file.apply_async(
                args=[file_path, language, custom_translator_id, glossary_path],
                priority=MEDIUM,
            )
            task_ids.append(task.id)
        return StreamingHttpResponse(
            # No cost because file translation costs are calculated in Celery task
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                llm,
                response_replacer=file_translation_generator(task_ids),
                dots=True,
                wrap_markdown=False,  # Because the generator already returns HTML
                remove_stop=True,
                stream_context=build_stream_context(request, "translate_file"),
            ),
            content_type="text/event-stream",
        )

    elif "azure" in translation_method:
        try:
            translated_text = translate_text_with_azure(
                user_message.text, language, custom_translator_id
            )
            return StreamingHttpResponse(
                streaming_content=htmx_stream(
                    chat,
                    response_message.id,
                    llm,
                    response_str=translated_text,
                    dots=True,
                    stream_context=build_stream_context(request, "translate_text"),
                ),
                content_type="text/event-stream",
            )
        except Exception as e:
            # If Azure translation fails, fall back to GPT
            logger.warning(f"Azure translation failed, falling back to GPT: {e}")
            translation_method = "gpt"

    # Fallback in case of invalid translation method
    raise Exception(f"Invalid translation method: {translation_method}")


def error_response(chat, response_message, error_message=None):
    """
    Send an error message to the user.
    """
    from otto.utils.common import generate_ai_error_summary

    llm = OttoLLM()
    error_id = str(uuid.uuid4())[:7]

    # Check if error_message is an Exception instance
    if isinstance(error_message, Exception):
        # Use AI to generate a user-friendly summary
        response_str = generate_ai_error_summary(error_message, error_id)
        logger.exception(
            "Error processing chat response",
            error_id=error_id,
            message_id=response_message.id,
            chat_id=chat.id,
        )
    else:
        # Traditional error message handling (for string messages)
        response_str = _("There was an error processing your request.")
        if error_message and settings.DEBUG:
            response_str += f"\n\n```\n{error_message}\n```\n\n"
        # Translatable string extracted for xgettext compatibility
        error_id_label = _("Error ID:")
        response_str += f" _({error_id_label} {error_id})_"
        logger.exception(
            "Error processing chat response",
            error_id=error_id,
            message_id=response_message.id,
            chat_id=chat.id,
        )

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            llm,
            response_str=response_str,
        ),
        content_type="text/event-stream",
    )
