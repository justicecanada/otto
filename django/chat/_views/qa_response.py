import asyncio

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import StreamingHttpResponse
from django.utils.translation import gettext as _

from asgiref.sync import sync_to_async
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
from pydantic import BaseModel, Field
from structlog import get_logger

from otto.priorities import HIGH, MEDIUM

from chat.llm import OttoLLM
from chat.utils import (
    build_stream_context,
    combine_batch_generators,
    combine_response_replacers,
    create_batches,
    generate_cost_warning,
    get_source_titles,
    group_sources_into_docs,
    htmx_stream,
    link_chat_files_to_library,
    num_tokens_from_string,
    qa_to_history,
    sort_by_max_score,
    stream_to_replacer,
)
from librarian.models import Document
from librarian.views import IN_PROGRESS_STATUSES

logger = get_logger(__name__)

# Maximum number of simultaneous LLM queries for multiple docs, sources, etc.
batch_size = getattr(settings, "PER_DOC_BATCH_SIZE", 5)


def build_query_info_details(reformulation, qa_mode):
    """
    Build a details string showing the optimized search and prompt.

    Returns a formatted string with search query and/or prompt.
    """
    lines = []

    if qa_mode == "rag" and reformulation.rag_query:
        lines.append(f"{_('Search')}: {reformulation.rag_query}")

    if reformulation.llm_prompt:
        lines.append(f"{_('Prompt')}: {reformulation.llm_prompt}")

    return "\n".join(lines)


async def _qa_stream_with_progress(
    chat,
    response_message,
    documents_qs,
    llm,
    switch_mode=False,
):
    """
    Async generator that handles the entire Q&A flow with progress events.

    Yields dicts with either:
    - {"progress_events": [...]} - Events to show in reasoning widget
    - {"text": "...", "source_nodes": [...]} - Text response with sources
    - Or delegates to inner generators that yield text

    This allows granular progress updates during:
    1. Query optimization (when qa_history is enabled)
    2. Document search (RAG retrieval)
    3. Response generation

    Progress events use replacement semantics (like reasoning_steps from API):
    each yield of progress_events replaces the previous one entirely.
    We maintain a list and yield the complete list each time.
    """
    qa_history = chat.options.qa_history
    qa_mode = chat.options.qa_mode

    # Track all events - each yield sends the complete list (replacement semantics)
    events = []

    # Phase 1: Query Optimization (only when qa_history is enabled)
    if qa_history:
        # Show "Optimizing queries" in progress
        events = [{"title": str(_("Optimizing queries")), "status": "in_progress"}]
        yield {
            "progress_events": events,
            "text": "",
            "is_reasoning": True,
        }

        # Perform query reformulation (this is the slow LLM call)
        reformulation = await sync_to_async(synthesize_retrieval_query)(
            chat, response_message, llm, documents_qs
        )

        # Check if no search needed (history-only response)
        if not reformulation.should_search:
            logger.info(
                "No search needed - using history-based answer",
                has_history_answer=bool(reformulation.history_answer),
            )

            # Show "Using conversation history" event (replaces previous)
            events = [
                {"title": str(_("Using conversation history")), "status": "complete"}
            ]
            yield {
                "progress_events": events,
                "text": "",
                "is_reasoning": False,
            }

            # Yield the history-based answer
            if reformulation.history_answer:
                yield reformulation.history_answer
            else:
                yield str(
                    _(
                        "Based on the context, I'm not sure how that relates. "
                        "To discuss something different, switch to chat mode."
                    )
                )
            return

        # Build details for "Optimizing queries" now that we have results
        query_details = build_query_info_details(reformulation, qa_mode)

        # Update "Optimizing queries" to complete with details
        events = [
            {
                "title": str(_("Optimizing queries")),
                "status": "complete",
                "details": query_details,
            }
        ]
    else:
        # No history optimization - use original question directly
        user_question = response_message.parent.text
        reformulation = QueryReformulation(
            should_search=True,
            llm_prompt=user_question,
            rag_query=user_question,
        )

    # Phase 2: Document Search / RAG Retrieval
    if qa_mode == "rag":
        events = events + [
            {"title": str(_("Searching for top excerpts")), "status": "in_progress"}
        ]
        yield {
            "progress_events": events,
            "text": "",
            "is_reasoning": True,
        }

    # Phase 3: Generate Response
    # Mark all progress events as complete before streaming response
    # Keep is_reasoning=True - the actual LLM response will set it to False
    # when reasoning is complete. This prevents the widget from prematurely
    # showing "Show processing steps" before API reasoning steps arrive.
    if events:
        # Update events to mark all as complete
        completed_events = []
        for event in events:
            completed_event = event.copy()
            completed_event["status"] = "complete"
            completed_events.append(completed_event)
        yield {
            "progress_events": completed_events,
            "text": "",
            "is_reasoning": True,  # Keep reasoning active until LLM response arrives
        }

    # Build the response generator based on mode
    if qa_mode != "rag":
        # Full Documents mode - no sources to track
        response_replacer = full_doc_answer(
            chat, response_message, llm, documents_qs, reformulation
        )
        async for response in response_replacer:
            yield response
    else:
        # RAG mode
        all_source_groups = []

        if chat.options.qa_process_mode == "combined_docs":
            answer_components = await sync_to_async(rag_answer)(
                chat,
                response_message,
                llm,
                documents_qs,
                chat.options.qa_scope,
                reformulation,
            )
            if answer_components:
                base_replacer = answer_components["response_replacer"]
                base_generator = answer_components["response_generator"]
                source_groups = answer_components.get("source_groups", [])

                # Yield source nodes for htmx_stream to save
                if source_groups:
                    yield {"source_nodes": source_groups, "text": ""}

                if base_replacer:
                    async for response in base_replacer:
                        yield response
                elif base_generator:
                    replacer = stream_to_replacer(base_generator)
                    async for response in replacer:
                        yield response
            else:
                yield str(
                    _(
                        "Sorry, I couldn't find any information about that. "
                        "Try using different keywords in your query."
                    )
                )
        else:
            # Per-document RAG mode
            doc_responses = []
            document_titles = [document.name for document in documents_qs]

            for document in documents_qs:
                if await sync_to_async(cache.get)(
                    f"stop_response_{response_message.id}", False
                ):
                    return

                answer_components = await sync_to_async(rag_answer)(
                    chat,
                    response_message,
                    llm,
                    [document],
                    chat.options.qa_scope,
                    reformulation,
                )

                if answer_components:
                    doc_response_replacer = answer_components["response_replacer"]
                    doc_source_groups = answer_components.get("source_groups", [])

                    # Collect sources
                    all_source_groups.extend(doc_source_groups)

                    # Always use response_replacer (qa_chat_stream now always uses replacer)
                    doc_responses.append(doc_response_replacer)
                else:
                    error_msg = str(
                        _(
                            "Sorry, I couldn't find any information about that in this document."
                        )
                    )
                    doc_responses.append(
                        stream_to_replacer([f"\n###### *{document.name}*\n{error_msg}"])
                    )

            # Yield all collected sources
            if all_source_groups:
                yield {"source_nodes": all_source_groups, "text": ""}

            if doc_responses:
                title_batches = create_batches(document_titles, batch_size)
                response_batches = create_batches(doc_responses, batch_size)
                batch_generators = [
                    combine_response_replacers(batch_responses, batch_titles)
                    for batch_responses, batch_titles in zip(
                        response_batches, title_batches
                    )
                ]
                combined = combine_batch_generators(
                    batch_generators, total_count=len(document_titles)
                )
                async for response in combined:
                    yield response
            else:
                yield str(
                    _(
                        "Sorry, I couldn't find any information about that. "
                        "Try selecting a different library or folder."
                    )
                )


class QueryReformulation(BaseModel):
    """Structured output for query reformulation with chat history."""

    should_search: bool = Field(
        description="Whether to perform document search. False for requests that only need the LLM to reformat/reorganize previous responses."
    )
    llm_prompt: str = Field(
        description="Reformulated prompt/question to pass to the LLM for generating the response"
    )
    rag_query: str = Field(
        description="Optimized search query for RAG retrieval (keyword + semantic search)"
    )
    history_answer: str | None = Field(
        default=None,
        description="Direct answer based on chat history. Only populated when should_search=False.",
    )


def qa_response(chat, response_message, skip_cost, switch_mode=False, request=None):
    """
    Answer a question using RAG on the selected library / data sources / documents.
    Handles two major flows:
      1. When files or a single URL are present on the user message, process and stream library updates.
      2. Otherwise, perform RAG/summarize against the selected Q&A scope.
    """
    model = chat.options.qa_model
    llm = OttoLLM(
        model,
        0.3,
        reasoning_effort=chat.options.qa_reasoning_effort,
        verbosity=chat.options.qa_verbosity,
        priority=HIGH,
    )

    user_message = response_message.parent
    files = user_message.sorted_files if user_message is not None else []

    # Detect if the message is a valid URL to quick-add to library
    adding_url = _is_valid_url(user_message.text)

    # If files or URL were provided, upsert/link them to the library and stream processing status.
    if len(files) > 0 or adding_url:
        logger.info(
            "qa_library_update_request_shape",
            file_count=len(files),
            adding_url=adding_url,
        )
        if adding_url:
            _link_url_to_library(chat.data_source, user_message.text, user_message)
        else:
            link_chat_files_to_library(
                files, user_message, chat.data_source, priority=MEDIUM
            )

        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                llm,
                response_replacer=_stream_library_updates(user_message, adding_url),
                wrap_markdown=True,
                dots=True,
                remove_stop=True,
                stream_context=build_stream_context(
                    request,
                    "qa_library_update",
                    file_count=len(files),
                    adding_url=adding_url,
                ),
            ),
            content_type="text/event-stream",
        )

    # Access library to refresh retention
    chat.options.qa_library.access()

    # Build document queryset for the selected scope
    documents_qs = _get_documents_for_scope(chat.options)
    if not documents_qs:
        response_str = _(
            "Sorry, I couldn't find any information about that. "
            "Try selecting more folders or documents, or try a different library."
        )
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                llm,
                response_str=response_str,
                switch_mode=switch_mode,
            ),
            content_type="text/event-stream",
        )

    # Check cost warning before starting the stream
    cost_response = generate_cost_warning(chat, response_message, skip_cost, request)
    if cost_response:
        return cost_response

    if hasattr(documents_qs, "count") and callable(documents_qs.count):
        try:
            document_count = documents_qs.count()
        except TypeError:
            document_count = len(documents_qs)
    else:
        document_count = len(documents_qs)
    logger.info(
        "qa_request_shape",
        qa_mode=chat.options.qa_mode,
        qa_process_mode=chat.options.qa_process_mode,
        qa_scope=chat.options.qa_scope,
        document_count=document_count,
        granular_toggle=getattr(chat.options, "qa_granular_toggle", False),
        qa_granularity=getattr(chat.options, "qa_granularity", None),
        qa_history=getattr(chat.options, "qa_history", False),
        batch_size=batch_size,
    )

    # Use the new async generator that handles progress events
    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            llm,
            response_replacer=_qa_stream_with_progress(
                chat,
                response_message,
                documents_qs,
                llm,
                switch_mode=switch_mode,
            ),
            switch_mode=switch_mode,
            dots=True,
            stream_context=build_stream_context(
                request,
                "qa_rag" if chat.options.qa_mode == "rag" else "qa_full_docs",
                document_count=document_count,
                qa_process_mode=chat.options.qa_process_mode,
            ),
        ),
        content_type="text/event-stream",
    )


def _is_valid_url(text: str) -> bool:
    if not text:
        return False
    validator = URLValidator()
    try:
        validator(text)
        logger.debug("Valid URL. Adding to chat library...")
        return True
    except ValidationError:
        return False


def _link_url_to_library(data_source, url_text, user_message):
    """
    Link a single URL to the library and queue processing.
    """
    existing_document = Document.objects.filter(
        data_source=data_source, url=url_text
    ).first()

    if not existing_document:
        document = Document.objects.create(
            data_source=data_source,
            url=url_text,
        )
        document.messages.add(user_message)
    else:
        document = existing_document
        document.messages.add(user_message)

    document.process(priority=MEDIUM)


def escape_ol_name(name):
    """
    Escape periods after leading numbers to prevent Markdown ordered list interpretation.
    E.g., "2. filename.pdf" becomes "2\\. filename.pdf"
    """
    if name and name[0].isdigit() and ". " in name:
        return name.replace(". ", r"\. ", 1)
    return name


async def _stream_library_updates(user_message, adding_url=False):
    """
    Async generator to stream status while documents are processing,
    then emit a completion message summarizing duplicates, errors,
    and new documents.
    """

    def _in_progress_count():
        return Document.objects.filter(
            messages=user_message, status__in=IN_PROGRESS_STATUSES
        ).count()

    processing_count = await sync_to_async(_in_progress_count)()
    # Translatable strings (extracted outside f-strings for xgettext compatibility)
    adding_msg = _("Adding to the Q&A library")
    still_processing_msg = _("file(s) still processing")
    while processing_count:
        if adding_url:
            yield adding_msg + "..."
        else:
            yield f"{adding_msg} ({processing_count} {still_processing_msg}...)"
        await asyncio.sleep(0.5)
        processing_count = await sync_to_async(_in_progress_count)()

    def _build_completion_message():
        # All sync ORM work in one block
        msg_docs = Document.objects.filter(messages=user_message)

        # Exclude container documents (ZIP files, etc.) that are not queryable in RAG
        # These documents exist for metadata but shouldn't be counted
        msg_docs_queryable = msg_docs.exclude(is_container=True)

        error_documents = list(msg_docs_queryable.filter(status="ERROR"))
        paused_documents = list(msg_docs_queryable.filter(status="PAUSED"))

        # Duplicate SUCCESS docs: associated to this message and at least one other message
        # We need to count ALL messages, not just within the filtered queryset
        duplicate_success = []
        for doc in msg_docs_queryable.filter(status="SUCCESS"):
            if doc.messages.count() > 1:
                duplicate_success.append(doc)

        # New SUCCESS docs: associated only to this message
        total_success = msg_docs_queryable.filter(status="SUCCESS").count()
        num_completed_documents = total_success - len(duplicate_success)

        parts = []

        if duplicate_success:
            names = [doc.filename for doc in duplicate_success if doc.filename]
            if names:
                escaped_names = [escape_ol_name(name) for name in names]
                parts.append(
                    _("The following document(s) already exist in the library:")
                    + "\n\n - "
                    + "\n\n - ".join(escaped_names)
                )

        if error_documents:
            errors = []
            for doc in error_documents:
                # Format: **filename** error message _(Error ID: xxx)_
                details = doc.status_details or _("Unknown error")
                # Check if there's an Error ID at the end and format it italic
                import re

                error_id_match = re.search(r"\(Error ID:?\s*([a-f0-9]+)\)\s*$", details)
                if error_id_match:
                    # Remove the Error ID from details and add it back formatted
                    details_without_id = details[: error_id_match.start()].strip()
                    error_id = error_id_match.group(1)
                    # Translatable string extracted for xgettext compatibility
                    error_id_label = _("Error ID:")
                    errors.append(
                        f"**{doc.filename}** {details_without_id} _({error_id_label} {error_id})_"
                    )
                else:
                    errors.append(f"**{doc.filename}** {details}")
            escaped_errors = [escape_ol_name(error) for error in errors]
            parts.append(
                _("Error processing the following document(s):")
                + "\n\n - "
                + "\n\n - ".join(escaped_errors)
            )

        if paused_documents:
            names = [doc.filename for doc in paused_documents if doc.filename]
            if names:
                escaped_names = [escape_ol_name(name) for name in names]
                parts.append(
                    _(
                        'The following large document(s) are paused and won\'t appear in "Top excerpts (RAG)" results yet. Open "Manage libraries" to manually force embedding for these documents if needed:'
                    )
                    + "\n\n - "
                    + "\n\n - ".join(escaped_names)
                )

        if adding_url and not error_documents:
            parts.append(_("URL ready for Q&A."))
        elif num_completed_documents > 0:
            parts.append(
                f"{num_completed_documents} " + _("new document(s) ready for Q&A.")
            )
        elif (
            not error_documents
            and not duplicate_success
            and num_completed_documents == 0
        ):
            parts.append(_("All documents already exist in the library."))

        return "\n\n".join(parts)

    completion_message = await sync_to_async(_build_completion_message)()
    yield completion_message


def _get_documents_for_scope(options):
    """
    Return a queryset of documents based on the selected QA scope. The query set can be customized further by applying exclusion and inclusion of further documents.
    Returns None if no scope can be resolved (to trigger an early message).
    """
    qa_scope = options.qa_scope
    if qa_scope == "data_sources":
        data_sources = options.qa_data_sources.all()
        additional_documents = options.qa_additional_documents.all()
        excluded_documents_qs = options.qa_excluded_documents.values("id")

        documents_qs = (
            Document.objects.filter(data_source__in=data_sources) | additional_documents
        ).distinct()

        documents_qs = documents_qs.exclude(id__in=excluded_documents_qs)

        return documents_qs
    elif qa_scope == "documents":
        # Preselected individual documents
        return options.qa_documents.all()
    elif qa_scope == "all":
        return Document.objects.filter(data_source__library=options.qa_library)
    # Fallback: invalid scope
    return None


async def qa_chat_stream(
    llm, system_prompt, user_message_content, context_str, query_str
):
    """
    Async generator that uses chat_stream for Q&A modes (full documents and RAG).
    Properly handles reasoning model output by separating thinking from text.

    Yields dicts compatible with htmx_stream:
    - {"text": "...", "reasoning_steps": [...], "is_reasoning": bool}

    If the combined input is too long for the model's context window, yields an
    error message with suggestions for the user.
    """
    from llama_index.core.llms import ChatMessage, MessageRole

    # Build the user message by substituting context and query into the template
    user_content = user_message_content.replace("{context_str}", context_str).replace(
        "{query_str}", query_str
    )

    # Check if content exceeds context window before sending to LLM
    total_input = system_prompt + user_content
    input_tokens = num_tokens_from_string(total_input)
    max_allowed = llm.max_input_tokens

    if input_tokens > max_allowed:
        error_message = _(
            "**Error:** The combined document content is too long for the selected AI model.\n\n"
            "The content has approximately {input_tokens:,} tokens, but the model can only process about {max_allowed:,} tokens.\n\n"
            "**You can try:**\n"
            "1. Using a model with a larger context window (e.g., GPT-4.1 series supports up to 1M tokens)\n"
            '2. Switching to "Top excerpts" mode instead of "Full documents" mode\n'
            '3. Selecting fewer documents or using "Separate" mode instead of "Combined"\n'
            "4. Using documents with less content"
        ).format(input_tokens=input_tokens, max_allowed=max_allowed)
        yield {"text": error_message}
        return

    chat_history = [
        ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
        ChatMessage(role=MessageRole.USER, content=user_content),
    ]

    async for response in llm.chat_stream(chat_history):
        # chat_stream yields dicts for reasoning models, strings otherwise
        yield response


# Alias for backward compatibility
full_doc_chat_stream = qa_chat_stream


def format_source_nodes_as_context(source_nodes):
    """
    Format retrieved source nodes as context string for the LLM.
    Uses the same format that LlamaIndex's response synthesizer would use.
    """
    from llama_index.core.schema import MetadataMode

    context_parts = []
    for node in source_nodes:
        # Get the node content with LLM-friendly metadata
        node_content = node.node.get_content(metadata_mode=MetadataMode.LLM)
        context_parts.append(node_content)

    return "\n\n---\n\n".join(context_parts)


def full_doc_answer(
    chat, response_message, llm, documents, reformulation, batch_size=5
):
    def combined_file_path_string(document):
        return str(document.file_path) + "\n" if document.file_path else ""

    def single_doc_file_path_string(document):
        return (str(document.file_path) + "\n---\n") if document.file_path else ""

    # Use reformulated LLM prompt
    query = reformulation.llm_prompt
    document_titles = [document.name for document in documents]
    total_extracted_chars = sum(
        len(document.extracted_text or "") for document in documents
    )
    batch_count = (
        1
        if chat.options.qa_process_mode == "combined_docs"
        else (len(document_titles) + batch_size - 1) // batch_size
    )
    logger.info(
        "qa_full_doc_request_shape",
        process_mode=chat.options.qa_process_mode,
        document_count=len(document_titles),
        total_extracted_chars=total_extracted_chars,
        batch_count=batch_count,
        batch_size=batch_size,
    )

    # Get mode metadata and integrate into template formatting
    mode_metadata = get_qa_mode_metadata(chat, documents)

    # Format the prompt template with ChatPromptTemplate.format_messages(),
    # passing mode_metadata as a kwarg so it can be used as a template variable
    formatted_messages = chat.options.qa_prompt_combined.format_messages(
        mode_metadata=mode_metadata
    )
    system_prompt = formatted_messages[0].content
    user_message_template = formatted_messages[1].content

    if chat.options.qa_process_mode == "combined_docs":
        # Combine all documents into one text, including the titles
        combined_documents = (
            "<document>\n"
            + "\n</document>\n<document>\n".join(
                [
                    f"# {document.name}\n{combined_file_path_string(document)}---\n{document.extracted_text}"
                    for document in documents
                ]
            )
            + "\n</document>"
        )
        response_replacer = full_doc_chat_stream(
            llm=llm,
            system_prompt=system_prompt,
            user_message_content=user_message_template,
            context_str=combined_documents,
            query_str=query,
        )
    else:
        title_batches = create_batches(document_titles, batch_size)
        doc_responses = [
            full_doc_chat_stream(
                llm=llm,
                system_prompt=system_prompt,
                user_message_content=user_message_template,
                context_str=f"{single_doc_file_path_string(document)}{document.extracted_text}",
                query_str=query,
            )
            for document in documents
            if not cache.get(f"stop_response_{response_message.id}", False)
        ]
        response_batches = create_batches(doc_responses, batch_size)
        batch_generators = [
            combine_response_replacers(
                batch_responses,
                batch_titles,
            )
            for batch_responses, batch_titles in zip(response_batches, title_batches)
        ]
        response_replacer = combine_batch_generators(
            batch_generators, total_count=len(document_titles)
        )

    return response_replacer


def get_qa_mode_metadata(chat, documents):
    """
    Generate metadata about the Q&A mode settings for inclusion in the prompt.
    This helps the LLM understand what mode it's operating in and provide better guidance.
    """
    qa_mode = chat.options.qa_mode
    qa_scope = chat.options.qa_scope
    qa_history = chat.options.qa_history

    # Determine mode description
    if qa_mode == "rag":
        mode_desc = "RAG mode (Top excerpts)"
    else:
        mode_desc = "Full documents mode"

    # Determine scope description
    doc_count = len(documents) if hasattr(documents, "__len__") else documents.count()
    if qa_scope == "all":
        scope_desc = f"entire library ({doc_count} documents)"
    elif qa_scope == "data_sources":
        scope_desc = f"selected folders ({doc_count} documents)"
    elif qa_scope == "documents":
        scope_desc = f"{doc_count} selected document{'s' if doc_count != 1 else ''}"
    else:
        scope_desc = f"{doc_count} documents"

    # Build metadata XML
    metadata = f"""<mode>{mode_desc}</mode>
  <scope>{scope_desc}</scope>
  <chat_history_enabled>{str(qa_history).lower()}</chat_history_enabled>"""

    return metadata


def get_chat_history_xml(chat, response_message):
    """
    Build chat history in XML format for inclusion in the prompt.
    Returns empty string if no history or qa_history is disabled.
    """
    if not chat.options.qa_history:
        return ""

    # Build chat history
    chat_history = qa_to_history(chat, response_message)

    # If there's no meaningful history (only system prompt or less), return empty
    if len(chat_history) <= 1:
        return ""

    # Format chat history as XML
    history_lines = []
    for msg in chat_history[1:]:  # Skip system message
        role = "user" if msg.role.value == "user" else "assistant"
        history_lines.append(f'  <message role="{role}">{msg.content}</message>')

    history_xml = "\n".join(history_lines)

    return f"""<chat_history>
{history_xml}
</chat_history>

"""


def synthesize_retrieval_query(
    chat, response_message, llm, documents_qs=None
) -> QueryReformulation:
    """
    Use the LLM to synthesize structured query reformulation based on chat history.
    Returns QueryReformulation with should_search, llm_prompt, and rag_query in a single LLM call.
    """
    # Force the LLM to GPT-4.1-mini for query reformulation
    llm = OttoLLM("gpt-4.1-mini")
    user_question = response_message.parent.text

    if not chat.options.qa_history:
        # No chat history, return original question for both
        return QueryReformulation(
            should_search=True,
            llm_prompt=user_question,
            rag_query=user_question,
        )

    # Build chat history
    chat_history = qa_to_history(chat, response_message)

    # Format chat history for the prompt
    history_str = "\n".join(
        [
            f"{msg.role.value}: {msg.content}"
            for msg in chat_history[1:]  # Skip system message
        ]
    )

    # Only pass document names when user explicitly selected individual documents
    # Don't enumerate potentially thousands of docs from a folder/library
    document_names = None
    if chat.options.qa_scope == "documents" and documents_qs is not None:
        document_names = [doc.name for doc in documents_qs[:20]]

    from chat.prompts import build_query_reformulation_prompt

    rewrite_prompt = build_query_reformulation_prompt(
        qa_mode=chat.options.qa_mode,
        qa_process_mode=chat.options.qa_process_mode,
        document_names=document_names,
    ).format(history_str=history_str, user_question=user_question)

    # Use structured predict to get the reformulation (single LLM call)
    sllm = llm.llm.as_structured_llm(QueryReformulation)
    reformulation = sllm.complete(rewrite_prompt)

    logger.info(
        "Reformulated query with chat history",
        original=user_question,
        should_search=reformulation.raw.should_search,
        llm_prompt=reformulation.raw.llm_prompt,
        rag_query=reformulation.raw.rag_query,
        has_history_answer=bool(reformulation.raw.history_answer),
        history_length=len(chat_history) - 1,
    )

    # Because we instantiated a new LLM, we need to create costs before returning
    llm.create_costs()

    return reformulation.raw


def rag_answer(
    chat, response_message, llm, documents, qa_scope, reformulation, batch_size=5
):
    batch_generators = []
    source_groups = []

    vector_store_table = chat.options.qa_library.uuid_hex
    top_k = chat.options.qa_topk

    # Don't include the top-level nodes (documents); they don't contain text
    filters = MetadataFilters(
        filters=[
            MetadataFilter(
                key="node_type",
                value="document",
                operator="!=",
            ),
        ]
    )
    if qa_scope != "all" or chat.options.qa_process_mode == "per_doc":
        filters.filters.append(
            MetadataFilter(
                key="doc_id",
                value=[document.uuid_hex for document in documents],
                operator="in",
            )
        )

    # Use HNSW only if index exists and is ready
    use_hnsw = chat.options.qa_library.use_hnsw_for_query()

    retriever = llm.get_retriever(
        vector_store_table,
        filters,
        top_k,
        chat.options.qa_vector_ratio,
        hnsw=use_hnsw,
    )

    # Get mode metadata and integrate into template formatting
    mode_metadata = get_qa_mode_metadata(chat, documents)

    # Format the prompt template with ChatPromptTemplate.format_messages(),
    # passing mode_metadata as a kwarg so it can be used as a template variable
    formatted_messages = chat.options.qa_prompt_combined.format_messages(
        mode_metadata=mode_metadata
    )
    system_prompt = formatted_messages[0].content
    user_message_template = formatted_messages[1].content

    # Use reformulated queries (already generated in qa_response)
    rag_query = reformulation.rag_query
    llm_prompt = reformulation.llm_prompt

    source_nodes = retriever.retrieve(rag_query)

    # Return None (not dict) when no sources found to distinguish from history-only response
    if len(source_nodes) == 0:
        return None

    # If we're stitching sources together into groups...
    if chat.options.qa_granular_toggle and chat.options.qa_granularity > 768:
        # Group nodes from the same doc together,
        # and ensure nodes WITHIN each doc are in reading order.
        # Need to do this if granularity is set to group multiple nodes together
        # AND/OR if "reading order" is enabled

        doc_groups = group_sources_into_docs(source_nodes)

        if chat.options.qa_source_order == "reading_order":
            # Reading order requires keeping docs together, so
            # sort documents by maximum node score within doc
            # before stitching nodes together
            doc_groups = sort_by_max_score(doc_groups)

        # Stitching
        for doc in doc_groups:
            current_source_group = []
            for next_source in doc:
                if num_tokens_from_string(
                    "\n\n".join(
                        [x.text for x in current_source_group] + [next_source.text]
                    ),
                    "cl100k_base",
                ) <= max(
                    num_tokens_from_string(next_source.text, "cl100k_base"),
                    chat.options.qa_granularity,
                ):
                    current_source_group.append(next_source)
                else:
                    source_groups.append(current_source_group)
                    current_source_group = [next_source]  # Start a new group

            # Add any remaining sources in current_source_group
            if current_source_group:
                source_groups.append(current_source_group)

        # If sorting by score, sort groups by max score within each one
        # (without keeping documents together across groups)
        if chat.options.qa_source_order == "score":
            source_groups = sort_by_max_score(source_groups)

    else:
        if chat.options.qa_source_order == "reading_order":
            # If we're not stitching anything, then we only need to group docs
            # if we're doing it in reading order
            doc_groups = group_sources_into_docs(source_nodes)
            doc_groups = sort_by_max_score(doc_groups)

            # Flatten newly-sorted source nodes
            source_nodes = [node for doc in doc_groups for node in doc]

        source_groups = [[source] for source in source_nodes]

    if not chat.options.qa_granular_toggle:
        # Use qa_chat_stream for proper reasoning support
        context_str = format_source_nodes_as_context(source_nodes)
        response_replacer = qa_chat_stream(
            llm=llm,
            system_prompt=system_prompt,
            user_message_content=user_message_template,
            context_str=context_str,
            query_str=llm_prompt,
        )
        response_generator = None

    else:
        # Granular mode: create chat_stream response for each source group
        responses = [
            qa_chat_stream(
                llm=llm,
                system_prompt=system_prompt,
                user_message_content=user_message_template,
                context_str=format_source_nodes_as_context(sources),
                query_str=llm_prompt,
            )
            for sources in source_groups
            if not cache.get(f"stop_response_{response_message.id}", False)
        ]
        titles = get_source_titles([sources[0] for sources in source_groups])
        title_batches = create_batches(titles, batch_size)
        response_batches = create_batches(responses, batch_size)
        batch_generators = [
            combine_response_replacers(
                batch_responses,
                batch_titles,
            )
            for batch_responses, batch_titles in zip(response_batches, title_batches)
        ]
        response_replacer = combine_batch_generators(
            batch_generators, total_count=len(titles)
        )
        response_generator = None

    logger.info(
        "qa_rag_request_shape",
        process_mode=chat.options.qa_process_mode,
        document_count=len(documents),
        retrieved_source_count=len(source_nodes),
        source_group_count=len(source_groups),
        granular_toggle=chat.options.qa_granular_toggle,
        batch_count=len(batch_generators),
        batch_size=batch_size,
    )

    return {
        "response_replacer": response_replacer,
        "response_generator": response_generator,
        "source_groups": source_groups,
        "batch_generators": batch_generators,
    }
