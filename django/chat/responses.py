import asyncio
import uuid

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import HttpResponse, StreamingHttpResponse
from django.template.loader import render_to_string
from django.utils.translation import gettext as _

from asgiref.sync import sync_to_async
from data_fetcher.util import get_request
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
from rules.contrib.views import objectgetter
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from chat.llm import OttoLLM
from chat.models import Message
from chat.prompts import current_time_prompt
from chat.tasks import translate_file
from chat.utils import (
    combine_batch_generators,
    combine_response_generators,
    combine_response_replacers,
    create_batches,
    estimate_cost_of_request,
    get_source_titles,
    group_sources_into_docs,
    htmx_stream,
    is_text_to_summarize,
    num_tokens_from_string,
    sort_by_max_score,
    stream_to_replacer,
    summarize_long_text,
    url_to_text,
)
from librarian.models import DataSource, Document, Library
from otto.models import Cost
from otto.utils.common import cad_cost
from otto.utils.decorators import permission_required

logger = get_logger(__name__)

batch_size = (
    5  # Maximum number of simultaneous LLM queries for multiple docs, sources, etc.
)


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def otto_response(request, message_id=None, switch_mode=False, skip_agent=False):
    """
    Stream a response to the user's message. Uses LlamaIndex to manage chat history.
    """
    response_message = Message.objects.get(id=message_id)
    skip_cost = request.GET.get("cost_approved", "false").lower() == "true"

    try:
        chat = response_message.chat
        mode = chat.options.mode

        estimate_cost = estimate_cost_of_request(chat, response_message)
        user_cost_this_month = cad_cost(
            Cost.objects.get_user_cost_this_month(request.user)
        )
        this_month_max = request.user.this_month_max
        if (estimate_cost + user_cost_this_month) >= this_month_max:
            return cost_warning_response(
                chat,
                response_message,
                estimate_cost,
                over_budget=True,
            )
        elif (estimate_cost >= settings.WARN_COST) and not skip_cost:
            return cost_warning_response(chat, response_message, estimate_cost)

        # For costing and logging. Contextvars are accessible anytime during the request
        # including in async functions (i.e. htmx_stream) and Celery tasks.
        bind_contextvars(message_id=message_id, feature=mode)

        agent_enabled = not skip_agent and mode == "chat" and chat.options.chat_agent
        if agent_enabled:
            return chat_agent(chat, response_message)
        if mode == "chat":
            return chat_response(chat, response_message, switch_mode=switch_mode)
        if mode == "summarize":
            return summarize_response(chat, response_message)
        if mode == "translate":
            return translate_response(chat, response_message)
        if mode == "qa":
            return qa_response(chat, response_message, switch_mode=switch_mode)
        else:
            return error_response(chat, response_message, _("Invalid mode."))
    except Exception as e:
        return error_response(chat, response_message, e)


def cost_warning_response(chat, response_message, estimate_cost, over_budget=False):

    formatted_cost = f"{estimate_cost:.2f}"
    if over_budget:
        cost_warning = _(
            f"This request is estimated to cost ${formatted_cost}, which exceeds your remaining monthly budget. Please contact an Otto administrator or wait until the 1st for the limit to reset."
        )
    else:
        cost_warning = _("This request could be expensive. Are you sure?")

    cost_warning_buttons = render_to_string(
        "chat/components/cost_warning_buttons.html",
        {
            "message_id": response_message.id,
            "estimate_cost": formatted_cost,
            "continue_button": not over_budget,
        },
    ).replace("\n", "")

    llm = OttoLLM()

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            llm,
            response_str=cost_warning,
            cost_warning_buttons=cost_warning_buttons,
        ),
        content_type="text/event-stream",
    )


def chat_response(
    chat,
    response_message,
    switch_mode=False,
):

    system_prompt = current_time_prompt() + chat.options.chat_system_prompt
    chat_history = [ChatMessage(role=MessageRole.SYSTEM, content=system_prompt)]
    chat_history += [
        ChatMessage(
            role=MessageRole.ASSISTANT if message.is_bot else MessageRole.USER,
            content=(
                message.text
                if not is_text_to_summarize(message)
                else "<text to summarize...>"
            ),
        )
        for message in chat.messages.all().order_by("date_created")
    ]

    if chat_history[-1].role == MessageRole.ASSISTANT and not chat_history[-1].content:
        # The last message is likely an empty placeholder - remove it to avoid errors
        chat_history.pop()

    model = chat.options.chat_model
    temperature = chat.options.chat_temperature

    llm = OttoLLM(model, temperature)

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

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            llm,
            response_replacer=llm.chat_stream(chat_history),
            switch_mode=switch_mode,
        ),
        content_type="text/event-stream",
    )


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def stop_response(request, message_id):
    """
    Stop the response to the user's message.
    """
    response_message = Message.objects.get(id=message_id)
    chat = response_message.chat

    cache.set(f"stop_response_{message_id}", True, timeout=60)

    return HttpResponse(200)


def summarize_response(chat, response_message):
    """
    Summarize the user's input text (or URL) and stream the response.
    If the summarization technique does not support streaming, send final response only.
    """
    user_message = response_message.parent
    files = user_message.sorted_files if user_message is not None else []
    summary_length = chat.options.summarize_style
    gender_neutral = chat.options.summarize_gender_neutral
    instructions = chat.options.summarize_instructions
    custom_summarize_prompt = chat.options.summarize_prompt
    target_language = chat.options.summarize_language
    model = chat.options.summarize_model

    llm = OttoLLM(model)
    error_str = ""

    if len(files) > 0:
        titles = [file.filename for file in files]
        responses = []
        for file in files:
            if cache.get(f"stop_response_{response_message.id}", False):
                break
            if not file.text:
                try:
                    file.extract_text(pdf_method="default")
                except Exception as e:
                    error_id = str(uuid.uuid4())[:7]
                    error_str = _(
                        "Error extracting text from file. Try copying and pasting the text."
                    )
                    error_str += f" _({_('Error ID')}: {error_id})_"
                    responses.append(stream_to_replacer([error_str]))
                    logger.exception(
                        f"Error extracting text from file:{e}",
                        error_id=error_id,
                        message_id=response_message.id,
                        chat_id=chat.id,
                    )
                    continue
            responses.append(
                summarize_long_text(
                    file.text,
                    llm,
                    summary_length,
                    target_language,
                    custom_summarize_prompt,
                    gender_neutral,
                    instructions,
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

        response_replacer = combine_batch_generators(batch_generators)
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                llm,
                response_replacer=response_replacer,
                dots=len(batch_generators) > 1,
            ),
            content_type="text/event-stream",
        )
    elif user_message.text == "":
        error_str = _("No text to summarize.")
    else:
        # Text input is a URL or plain text
        url_validator = URLValidator()
        try:
            url_validator(user_message.text)
            text_to_summarize = url_to_text(user_message.text)
            # Check if response text is too short (most likely a website blocking Otto)
            if len(text_to_summarize.split()) < 35:
                error_str = _(
                    "Couldn't retrieve the webpage. The site might block bots. Try copy & pasting the webpage here."
                )
        except ValidationError:
            text_to_summarize = user_message.text

    if error_str:
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                llm,
                response_str=error_str,
            ),
            content_type="text/event-stream",
        )

    response = summarize_long_text(
        text_to_summarize,
        llm,
        summary_length,
        target_language,
        custom_summarize_prompt,
        gender_neutral,
        instructions,
    )
    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            llm,
            response_replacer=response,
        ),
        content_type="text/event-stream",
    )


def translate_response(chat, response_message):
    """
    Translate the user's input text and stream the response.
    If the translation technique does not support streaming, send final response only.
    """
    llm = OttoLLM()
    user_message = response_message.parent
    files = user_message.sorted_files if user_message is not None else []
    language = chat.options.translate_language

    def file_msg(response_message, total_files):
        return render_to_string(
            "chat/components/message_files.html",
            context={"message": response_message, "total_files": total_files},
        )

    async def file_translation_generator(task_ids):
        yield "<p>" + _("Initiating translation...") + "</p>"
        any_task_done = False
        try:
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
                        # Refresh the response message from the database
                        await sync_to_async(response_message.refresh_from_db)()
                if not any_task_done:
                    yield "<p>" + _("Translating file") + f" 1/{len(files)}...</p>"
                else:
                    yield await sync_to_async(file_msg)(response_message, len(files))
        except:
            error_id = str(uuid.uuid4())[:7]
            error_str = _("Error translating files.")
            error_str += f" _({_('Error ID')}: {error_id})_"
            logger.exception(
                f"Error translating files",
                error_id=error_id,
                message_id=response_message.id,
                chat_id=chat.id,
            )
            yield error_str
            # raise Exception(_("Error translating files."))

    if len(files) > 0:
        # Initiate the Celery task for translating each file with Azure
        task_ids = []
        for file in files:
            # file is a django ChatFile object with property "file" that is a FileField
            # We need the path of the file to pass to the Celery task
            file_path = file.saved_file.file.path
            task = translate_file.delay(file_path, language)
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
            ),
            content_type="text/event-stream",
        )
    # Simplest method: Just use LLM to translate input text.
    # Note that long plain-translations frequently fail due to output token limits (~4k)
    # It is not easy to check for this in advance, so we just try and see what happens
    # TODO: Evaluate vs. Azure translator (cost and quality)
    target_language = {"en": "English", "fr": "French"}[language]
    translate_prompt = (
        "Translate the following text to English (Canada):\n"
        "Bonjour, comment ça va?"
        "\n---\nTranslation: Hello, how are you?\n"
        "Translate the following text to French (Canada):\n"
        "What size is the file?\nPlease answer in bytes."
        "\n---\nTranslation: Quelle est la taille du fichier?\nVeuillez répondre en octets.\n"
        f"Translate the following text to {target_language} (Canada):\n"
        f"{user_message.text}"
        "\n---\nTranslation: "
    )

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            llm,
            response_replacer=llm.stream(translate_prompt),
        ),
        content_type="text/event-stream",
    )


def qa_response(chat, response_message, switch_mode=False):
    """
    Answer a question using RAG on the selected library / data sources / documents
    """
    model = chat.options.qa_model
    llm = OttoLLM(model, 0.1)

    batch_generators = []

    user_message = response_message.parent
    files = user_message.sorted_files if user_message is not None else []

    # Quick-add URL to library
    url_validator = URLValidator()
    try:
        url_validator(user_message.text)
        adding_url = True
        logger.debug("Valid URL. Adding to chat library...")
    except ValidationError:
        adding_url = False

    async def add_files_to_library():
        message_created_at = response_message.date_created
        ds = chat.data_source
        processing_count = await sync_to_async(
            lambda: ds.documents.filter(status__in=["INIT", "PROCESSING"]).count()
        )()
        while processing_count:
            if adding_url:
                yield _("Adding to the Q&A library") + "..."
            else:
                yield f'{_("Adding to the Q&A library")} ({processing_count} {_("file(s) still processing")}...)'
            await asyncio.sleep(0.5)
            processing_count = await sync_to_async(
                lambda: ds.documents.filter(status__in=["INIT", "PROCESSING"]).count()
            )()

        error_documents = await sync_to_async(
            lambda: list(
                ds.documents.filter(status="ERROR", created_at__gt=message_created_at)
            )
        )()
        num_completed_documents = await sync_to_async(
            lambda: ds.documents.filter(
                status="SUCCESS", created_at__gt=message_created_at
            ).count()
        )()
        if error_documents:
            error_string = _("Error processing the following document(s):")
            doc_names_for_error = [doc.filename for doc in error_documents]
            error_docs_joined = "\n\n - " + "\n\n - ".join(doc_names_for_error)
            error_string += error_docs_joined
            if len(error_documents) != len(files):
                error_string += f"\n\n{num_completed_documents} "
                error_string += _("new document(s) ready for Q&A.")
            yield error_string
        elif adding_url:
            yield _("URL ready for Q&A.")
        else:
            yield f"{num_completed_documents} " + _("new document(s) ready for Q&A.")

    if len(files) > 0 or adding_url:
        for file in files:
            existing_document = Document.objects.filter(
                data_source=chat.data_source,
                filename=file.filename,
                saved_file__sha256_hash=file.saved_file.sha256_hash,
            ).first()
            # Skip if filename and hash are the same, but reprocess if ERROR status
            if existing_document:
                if existing_document.status == "ERROR":
                    existing_document.process()
                continue
            document = Document.objects.create(
                data_source=chat.data_source,
                saved_file=file.saved_file,
                filename=file.filename,
            )
            document.process()
        if adding_url:
            existing_document = Document.objects.filter(
                data_source=chat.data_source, url=user_message.text
            ).first()
            if not existing_document:
                document = Document.objects.create(
                    data_source=chat.data_source,
                    url=user_message.text,
                )
            else:
                document = existing_document
            # URLs are always re-processed
            document.process()
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                llm,
                response_replacer=add_files_to_library(),
                wrap_markdown=True,
                dots=True,
                remove_stop=True,
            ),
            content_type="text/event-stream",
        )

    # Access library to update accessed_at field in order to reset the 30 days for deletion of unused libraries
    chat.options.qa_library.access()
    # Apply filters if we are in qa mode and specific data sources are selected
    qa_scope = chat.options.qa_scope
    filter_documents = None
    if qa_scope == "data_sources":
        data_sources = chat.options.qa_data_sources.all()
        filter_documents = Document.objects.filter(data_source__in=data_sources)
    elif qa_scope == "documents":
        filter_documents = chat.options.qa_documents.all()
    if qa_scope != "all" and not filter_documents.exists():
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

    # Summarize mode
    if chat.options.qa_mode in ["summarize", "summarize_combined"]:
        if not filter_documents:
            filter_documents = Document.objects.filter(
                data_source__library=chat.options.qa_library
            )
        document_titles = [document.name for document in filter_documents]
        if chat.options.qa_mode == "summarize_combined":
            # Combine all documents into one text, including the titles
            combined_documents = (
                "<document>\n"
                + "\n</document>\n<document>\n".join(
                    [
                        f"# {title}\n---\n{document.extracted_text}"
                        for title, document in zip(document_titles, filter_documents)
                    ]
                )
                + "\n</document>"
            )
            response_replacer = llm.tree_summarize(
                context=combined_documents,
                query=user_message.text,
                template=chat.options.qa_prompt_combined,
            )
        else:
            # Use summarization on each of the documents
            summary_responses = [
                llm.tree_summarize(
                    context=document.extracted_text,
                    query=user_message.text,
                    template=chat.options.qa_prompt_combined,
                )
                for document in filter_documents
                if not cache.get(f"stop_response_{response_message.id}", False)
            ]
            title_batches = create_batches(
                document_titles, batch_size
            )  # TODO: test batch size
            response_batches = create_batches(summary_responses, batch_size)
            batch_generators = [
                combine_response_replacers(
                    batch_responses,
                    batch_titles,
                )
                for batch_responses, batch_titles in zip(
                    response_batches, title_batches
                )
            ]
            response_replacer = combine_batch_generators(
                batch_generators,
            )
        response_generator = None
        source_groups = None

    else:
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
        if qa_scope != "all":
            filters.filters.append(
                MetadataFilter(
                    key="doc_id",
                    value=[document.uuid_hex for document in filter_documents],
                    operator="in",
                )
            )
        retriever = llm.get_retriever(
            vector_store_table,
            filters,
            top_k,
            chat.options.qa_vector_ratio,
        )
        synthesizer = llm.get_response_synthesizer(chat.options.qa_prompt_combined)
        input = response_message.parent.text
        source_nodes = retriever.retrieve(input)

        # For debugging: Shows how nodes are presented to the LLM
        # from llama_index.core.schema import MetadataMode

        # for node in source_nodes:
        #     print(node.get_content(metadata_mode=MetadataMode.LLM))

        if len(source_nodes) == 0:
            response_str = _(
                "Sorry, I couldn't find any information about that. Try selecting a different library or folder."
            )
            return StreamingHttpResponse(
                # Although there are no LLM costs, there is still a query embedding cost
                streaming_content=htmx_stream(
                    chat,
                    response_message.id,
                    llm,
                    response_str=response_str,
                    switch_mode=switch_mode,
                ),
                content_type="text/event-stream",
            )

        # If we're stitching sources together into groups...
        if chat.options.qa_granularity > 768:
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
            source_groups = []
            for doc in doc_groups:
                current_source_group = []
                for next_source in doc:
                    if (
                        num_tokens_from_string(
                            "\n\n".join(
                                [x.text for x in current_source_group]
                                + [next_source.text]
                            )
                        )
                        <= chat.options.qa_granularity
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
        if chat.options.qa_answer_mode != "per-source":
            response = synthesizer.synthesize(query=input, nodes=source_nodes)
            response_generator = response.response_gen
            response_replacer = None

        else:
            responses = [
                synthesizer.synthesize(query=input, nodes=sources).response_gen
                for sources in source_groups
                if not cache.get(f"stop_response_{response_message.id}", False)
            ]
            titles = get_source_titles([sources[0] for sources in source_groups])
            title_batches = create_batches(titles, batch_size)
            response_batches = create_batches(responses, batch_size)
            batch_generators = [
                combine_response_generators(
                    batch_responses,
                    batch_titles,
                    input,
                    llm,
                    chat.options.qa_prune,
                )
                for batch_responses, batch_titles in zip(
                    response_batches, title_batches
                )
            ]
            response_replacer = combine_batch_generators(
                batch_generators,
                pruning=chat.options.qa_prune,
            )
            response_generator = None

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            llm,
            response_generator=response_generator,
            response_replacer=response_replacer,
            source_nodes=source_groups,
            switch_mode=switch_mode,
            dots=len(batch_generators) > 1,
        ),
        content_type="text/event-stream",
    )


def error_response(chat, response_message, error_message=None):
    """
    Send an error message to the user.
    """
    llm = OttoLLM()
    response_str = _("There was an error processing your request.")
    error_id = str(uuid.uuid4())[:7]

    if error_message and settings.DEBUG:
        response_str += f"\n\n```\n{error_message}\n```\n\n"
    response_str += f" _({_('Error ID')}: {error_id})_"
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


def chat_agent(chat, response_message):
    """
    Select the mode for the chat response.
    """
    bind_contextvars(feature="chat_agent")
    user_message = response_message.parent
    if user_message is None:
        return error_response(chat, response_message, _("No user message found."))

    user_text = user_message.text
    if len(user_text) > 500:
        user_text = user_text[:500] + "..."
    llm = OttoLLM()
    prompt = (
        "Your role is to determine the best mode to handle the user's message.\n"
        "The available modes and their descriptions are below:\n"
        "- 'qa' mode: Question answering over previously uploaded 'document libraries'.\n"
        "  Available document libraries:\n"
    )
    available_libraries = [
        library
        for library in Library.objects.all()
        if chat.user.has_perm("librarian.view_library", library)
    ]
    for library in available_libraries:
        prompt += f"  - {library.name} (id={library.id}){(': ' + library.description) if library.description else ''}\n"
        if not library.is_personal_library:
            # List the data sources in the library
            data_sources = DataSource.objects.filter(library=library)
            for data_source in data_sources:
                prompt += f"    - {data_source.name}\n"
    prompt += (
        "- 'chat' mode: General purpose interaction with LLM, like ChatGPT.\n\n"
        "Based on the user's message (below), respond with the appropriate mode and library, if any. "
        "For questions unrelated to the available libraries, prefer to use 'chat' mode.\n"
        "User's message:\n\n"
        f"{user_text}\n\n"
        "Mode: (qa, chat)\n"
        "Library ID: (if qa mode)"
    )
    mode_response_raw = llm.complete(prompt)
    # print(
    #     "Mode selected based on the prompt and response:\n",
    #     prompt,
    #     "\n\n",
    #     mode_response_raw,
    # )
    original_mode = chat.options.mode
    if "qa" in mode_response_raw:
        mode = "qa"
        try:
            library_id = int("".join(c for c in mode_response_raw if c.isdigit()))
        except:
            library_id = None
    else:
        mode = "chat"
        library_id = None
    chat.options.mode = mode
    if (
        library_id
        and Library.objects.filter(id=library_id).exists()
        and chat.user.has_perm(
            "librarian.view_library", Library.objects.get(id=library_id)
        )
    ):
        chat.options.qa_library_id = library_id
        chat.options.qa_scope = "all"
        chat.options.qa_data_sources.clear()
        chat.options.qa_documents.clear()
    chat.options.save()
    llm.create_costs()
    request = get_request()
    return otto_response(
        request,
        message_id=response_message.id,
        switch_mode=mode if mode != original_mode else False,
        skip_agent=True,
    )
