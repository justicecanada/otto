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
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
from rules.contrib.views import objectgetter
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from chat.llm import OttoLLM
from chat.models import Message
from chat.tasks import translate_file
from chat.utils import (
    chat_to_history,
    combine_batch_generators,
    combine_response_generators,
    combine_response_replacers,
    create_batches,
    estimate_cost_of_request,
    get_source_titles,
    group_sources_into_docs,
    htmx_stream,
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

# Maximum number of simultaneous LLM queries for multiple docs, sources, etc.
batch_size = 5


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
    model = chat.options.chat_model
    temperature = chat.options.chat_temperature
    reasoning_effort = chat.options.chat_reasoning_effort
    llm = OttoLLM(model, temperature, reasoning_effort=reasoning_effort)

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
    summarize_prompt = chat.options.summarize_prompt
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
                    error_str += f" _({_('Error ID:')} {error_id})_"
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
        summarize_prompt,
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
            error_str += f" _({_('Error ID:')} {error_id})_"
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
        # Include duplicates that were already SUCCESS before this request
        filenames = [f.filename for f in files]
        hashes = [f.saved_file.sha256_hash for f in files]
        duplicate_success = await sync_to_async(
            lambda: list(
                ds.documents.filter(
                    filename__in=filenames,
                    saved_file__sha256_hash__in=hashes,
                    status="SUCCESS",
                    created_at__lte=message_created_at,
                )
            )
        )()

        completion_message = ""
        if duplicate_success:
            duplicate_doc_names = [f"{doc.filename}" for doc in duplicate_success]
            duplicate_docs_joined = "\n\n - " + "\n\n - ".join(duplicate_doc_names)
            completion_message += (
                _("The following document(s) already exist in the library:")
                + duplicate_docs_joined
            ) + "\n\n"
        if error_documents:
            error_string = _("Error processing the following document(s):")
            doc_errors = [
                f"{doc.filename} _{doc.status_details}_" for doc in error_documents
            ]
            error_string += "\n\n - " + "\n\n - ".join(doc_errors)
            completion_message += error_string + "\n\n"
        if adding_url:
            completion_message += _("URL ready for Q&A.")
        elif num_completed_documents > 0:
            completion_message += f"{num_completed_documents} " + _(
                "new document(s) ready for Q&A."
            )

        yield completion_message

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

    if not filter_documents:
        filter_documents = Document.objects.filter(
            data_source__library=chat.options.qa_library
        )

    # Summarize mode
    if chat.options.qa_mode != "rag":
        response_generator = None
        source_groups = None
        response_replacer = full_doc_answer(
            chat, response_message, llm, filter_documents
        )

    else:
        if chat.options.qa_process_mode == "combined_docs":
            answer_components = rag_answer(
                chat, response_message, llm, filter_documents, qa_scope
            )
            response_replacer = answer_components["response_replacer"]
            response_generator = answer_components["response_generator"]
            source_groups = answer_components["source_groups"]
            batch_generators = answer_components["batch_generators"]
        else:
            source_groups = []
            doc_responses = []
            document_titles = [document.name for document in filter_documents]
            title_batches = create_batches(document_titles, batch_size)
            for document in filter_documents:
                answer_components = rag_answer(
                    chat, response_message, llm, [document], qa_scope
                )

                if answer_components:
                    doc_response_replacer = answer_components["response_replacer"]
                    doc_response_generator = answer_components["response_generator"]
                    doc_source_groups = answer_components["source_groups"]
                    doc_batch_generators = answer_components["batch_generators"]
                    doc_responses.append(
                        doc_response_replacer
                        if chat.options.qa_granular_toggle
                        else doc_response_generator
                    )

                    source_groups.extend(doc_source_groups)
                    batch_generators.extend(doc_batch_generators)

                else:
                    response_str = _(
                        "Sorry, I couldn't find any information about that in this document."
                    )
                    doc_responses.append(
                        stream_to_replacer(
                            [f"\n###### *{document.name}*\n{response_str}"]
                        )
                    )

            if len(source_groups) > 0:
                response_batches = create_batches(doc_responses, batch_size)
                if not batch_generators:
                    batch_generators = [
                        combine_response_generators(
                            batch_responses, batch_titles, input, llm, prune=False
                        )
                        for batch_responses, batch_titles in zip(
                            response_batches, title_batches
                        )
                    ]

                response_replacer = combine_batch_generators(batch_generators)
                response_generator = None

    if source_groups == []:
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


def full_doc_answer(chat, response_message, llm, documents, batch_size=5):
    template = chat.options.qa_prompt_combined
    query = response_message.parent.text
    document_titles = [document.name for document in documents]

    doc_responses = [
        llm.tree_summarize(context=doc, query=query, template=template)
        for doc in documents
        if not cache.get(f"stop_response_{response_message.id}", False)
    ]

    if chat.options.qa_process_mode == "combined_docs":
        # Combine all documents into one text, including the titles
        combined_documents = (
            "<document>\n"
            + "\n</document>\n<document>\n".join(
                [
                    f"# {title}\n---\n{document.extracted_text}"
                    for title, document in zip(document_titles, documents)
                ]
            )
            + "\n</document>"
        )
        response_replacer = llm.tree_summarize(
            context=combined_documents,
            query=query,
            template=chat.options.qa_prompt_combined,
        )
    else:
        title_batches = create_batches(document_titles, batch_size)
        doc_responses = [
            llm.tree_summarize(
                context=document.extracted_text,
                query=query,
                template=chat.options.qa_prompt_combined,
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
        response_replacer = combine_batch_generators(batch_generators)

    return response_replacer


def rag_answer(chat, response_message, llm, documents, qa_scope, batch_size=5):
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
        return

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
            for batch_responses, batch_titles in zip(response_batches, title_batches)
        ]
        response_replacer = combine_batch_generators(
            batch_generators,
            pruning=chat.options.qa_prune,
        )
        response_generator = None

    return {
        "response_replacer": response_replacer,
        "response_generator": response_generator,
        "source_groups": source_groups,
        "batch_generators": batch_generators,
    }


def error_response(chat, response_message, error_message=None):
    """
    Send an error message to the user.
    """
    llm = OttoLLM()
    response_str = _("There was an error processing your request.")
    error_id = str(uuid.uuid4())[:7]

    if error_message and settings.DEBUG:
        response_str += f"\n\n```\n{error_message}\n```\n\n"
    response_str += f" _({_('Error ID:')} {error_id})_"
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
