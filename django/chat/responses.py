import asyncio

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import HttpResponse, StreamingHttpResponse
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

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
    htmx_stream,
    num_tokens_from_string,
    summarize_long_text,
    summarize_long_text_async,
    url_to_text,
)
from librarian.models import DataSource, Document, Library
from otto.utils.decorators import permission_required

logger = get_logger(__name__)


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def otto_response(request, message_id=None, switch_mode=False, skip_agent=False):
    """
    Stream a response to the user's message. Uses LlamaIndex to manage chat history.
    """
    response_message = Message.objects.get(id=message_id)
    chat = response_message.chat
    mode = chat.options.mode

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
        return error_response(chat, response_message)


def chat_response(
    chat,
    response_message,
    switch_mode=False,
):

    def is_text_to_summarize(message):
        return message.mode == "summarize" and not message.is_bot

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

    model = chat.options.chat_model
    temperature = chat.options.chat_temperature

    llm = OttoLLM(model, temperature)

    tokens = num_tokens_from_string(
        " ".join(message.content for message in chat_history)
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
            ),
            content_type="text/event-stream",
            switch_mode=switch_mode,
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
    custom_summarize_prompt = chat.options.summarize_prompt
    target_language = chat.options.summarize_language
    model = chat.options.summarize_model

    llm = OttoLLM(model)

    # TODO: Extracting text from file may incur Azure Document AI costs.
    # Need to refactor extract_text to create Cost object with correct user and mode.
    async def multi_summary_generator():
        full_text = ""
        for i, file in enumerate(files):
            full_text += f"**{file.filename}**\n\n"
            yield full_text
            if not file.text:
                await sync_to_async(file.extract_text)(fast=True)
            response_stream = await summarize_long_text_async(
                file.text,
                llm,
                summary_length,
                target_language,
                custom_summarize_prompt,
            )
            async for summary in response_stream:
                full_text_with_summary = full_text + summary
                yield full_text_with_summary

            full_text = full_text_with_summary
            if i < len(files) - 1:
                full_text += "\n\n-----\n"
            yield full_text
            await asyncio.sleep(0)

    if len(files) > 0:
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                llm,
                response_replacer=multi_summary_generator(),
                dots=True,
            ),
            content_type="text/event-stream",
        )
    elif user_message.text == "":
        summary = _("No text to summarize.")
    else:
        url_validator = URLValidator()
        try:
            url_validator(user_message.text)
            text_to_summarize = url_to_text(user_message.text)
        except ValidationError:
            text_to_summarize = user_message.text

        # Check if response text is too short (most likely a website blocking Otto)
        if len(text_to_summarize.split()) < 35:
            summary = _(
                "Couldn't retrieve the webpage. The site might block bots. Try copy & pasting the webpage here."
            )
        else:
            response = summarize_long_text(
                text_to_summarize,
                llm,
                summary_length,
                target_language,
                custom_summarize_prompt,
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

    # This will only be reached in an error case
    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat, response_message.id, llm, response_str=summary
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
        while task_ids:
            # To prevent constantly checking the task status, we sleep for a bit
            # File translation is very slow so every few seconds is plenty.
            await asyncio.sleep(2)
            for task_id in task_ids.copy():
                task = translate_file.AsyncResult(task_id)
                # If the task is not running, remove it from the list
                if task.state != "PENDING":
                    task_ids.remove(task_id)
                    # Refresh the response message from the database
                    await sync_to_async(response_message.refresh_from_db)()
            if len(task_ids) == len(files):
                yield "<p>" + _("Translating file") + f" 1/{len(files)}...</p>"
            else:
                yield await sync_to_async(file_msg)(response_message, len(files))

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
                format=False,  # Because the generator already returns HTML
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


# AC-20: Only read and retrieve information from external sources (libraries and data sources) without modifying them
def qa_response(chat, response_message, switch_mode=False):
    """
    Answer a question using RAG on the selected library / data sources / documents
    """
    model = chat.options.qa_model
    llm = OttoLLM(model, 0.1)

    user_message = response_message.parent
    files = user_message.sorted_files if user_message is not None else []

    async def add_files_to_library():
        ds = chat.data_source
        processing_count = await sync_to_async(
            lambda: ds.documents.filter(status__in=["INIT", "PROCESSING"]).count()
        )()
        while processing_count:
            yield _(
                "Adding files to the Library"
            ) + f" ({len(files)-processing_count+1}/{len(files)})..."
            await asyncio.sleep(0.5)
            processing_count = await sync_to_async(
                lambda: ds.documents.filter(status__in=["INIT", "PROCESSING"]).count()
            )()

        yield f"{len(files)} " + _("new file(s) ready for Q&A.")

    if len(files) > 0:
        for file in files:
            document = Document.objects.create(
                data_source=chat.data_source,
                file=file.saved_file,
                filename=file.filename,
            )
            document.process()
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                llm,
                response_replacer=add_files_to_library(),
                dots=True,
            ),
            content_type="text/event-stream",
        )

    # Apply filters if we are in qa mode and specific data sources are selected
    qa_scope = chat.options.qa_scope
    if qa_scope == "data_sources":
        data_sources = chat.options.qa_data_sources.all()
        filter_documents = Document.objects.filter(data_source__in=data_sources)
    elif qa_scope == "documents":
        filter_documents = chat.options.qa_documents.all()
    if qa_scope != "all" and not filter_documents.exists():
        response_str = _(
            "Sorry, I couldn't find any information about that. "
            "Try selecting more data sources or documents, or try a different library."
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

    if len(source_nodes) == 0:
        response_str = _(
            "Sorry, I couldn't find any information about that. Try selecting a different library or data source."
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

    response = synthesizer.synthesize(query=input, nodes=source_nodes)

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            llm,
            response_generator=response.response_gen,
            source_nodes=response.source_nodes,
            switch_mode=switch_mode,
        ),
        content_type="text/event-stream",
    )


def error_response(chat, response_message):
    """
    Send an error message to the user.
    """
    llm = OttoLLM()
    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            llm,
            response_str=_("Sorry, this isn't working right now."),
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
        return error_response(chat, response_message)

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
