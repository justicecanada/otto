import asyncio

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import HttpResponse, StreamingHttpResponse
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from asgiref.sync import sync_to_async
from llama_index.core.llms import ChatMessage, MessageRole
from rules.contrib.views import objectgetter
from structlog import get_logger

from chat.llm import OttoLLM
from chat.models import Message
from chat.tasks import translate_file
from chat.utils import (
    htmx_stream,
    num_tokens_from_string,
    summarize_long_text,
    summarize_long_text_async,
    url_to_text,
)
from librarian.models import Document
from otto.utils.decorators import permission_required

logger = get_logger(__name__)


@permission_required("chat.access_message", objectgetter(Message, "message_id"))
def otto_response(request, message_id=None):
    """
    Stream a response to the user's message. Uses LangChain to manage chat history.
    """
    response_message = Message.objects.get(id=message_id)
    chat = response_message.chat
    assert chat.user_id == request.user.id
    mode = chat.options.mode
    if mode == "chat":
        return chat_response(chat, response_message)
    if mode == "summarize":
        return summarize_response(chat, response_message)
    if mode == "translate":
        return translate_response(chat, response_message)
    if mode == "qa":
        return qa_response(chat, response_message)
    else:
        return error_response(chat, response_message)


def chat_response(chat, response_message, eval=False):

    def is_text_to_summarize(message):
        return message.mode == "summarize" and not message.is_bot

    system_prompt = chat.options.chat_system_prompt
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
        # In this case, just return an error
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                response_str=_(
                    "**Error:** The chat is too long for this AI model.\n\nYou can try: \n"
                    "1. Starting a new chat\n"
                    "2. Using summarize mode, which can handle longer texts\n"
                    "3. Using a different model\n"
                ),
            ),
            content_type="text/event-stream",
        )

    # TODO: Update eval stuff
    # if eval:
    #     # Just return the full response and an empty list representing source nodes
    #     return llm.invoke(chat_history).content, []

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            response_replacer=llm.chat_stream(chat_history),
            llm=llm,
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
    assert chat.user_id == request.user.id

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
                response_replacer=multi_summary_generator(),
                dots=True,
                llm=llm,
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
                    response_replacer=response,
                    llm=llm,
                ),
                content_type="text/event-stream",
            )

    return StreamingHttpResponse(
        streaming_content=htmx_stream(chat, response_message.id, response_str=summary),
        content_type="text/event-stream",
    )


def translate_response(chat, response_message):
    """
    Translate the user's input text and stream the response.
    If the translation technique does not support streaming, send final response only.
    """
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
            task = translate_file.delay(file_path, response_message.id, language)
            task_ids.append(task.id)
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                response_replacer=file_translation_generator(task_ids),
                dots=True,
                format=False,  # Because the generator already returns HTML
                save_message=False,  # Because the generator already saves messages
            ),
            content_type="text/event-stream",
        )
    # Simplest method: Just use LLM to translate input text.
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

    llm = OttoLLM()
    # Note that long plain-translations frequently fail due to output token limits
    # It is not easy to check for this in advance, so we just try and see what happens

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            response_replacer=llm.stream(translate_prompt),
            llm=llm,
        ),
        content_type="text/event-stream",
    )


def qa_response(chat, response_message, eval=False):
    """
    Answer the user's question using a specific vector store table.
    """
    from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters

    # Apply filters if we are in qa mode and specific data sources are selected
    data_sources = chat.options.qa_data_sources.all()
    max_data_sources = chat.options.qa_library.data_sources.count()
    print(f"Data sources: {data_sources}, max: {max_data_sources}")
    if not Document.objects.filter(data_source__in=data_sources).exists():
        response_str = _(
            "Sorry, I couldn't find any information about that. Try selecting a different library or data source."
        )
        if eval:
            return response_str, []
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat,
                response_message.id,
                response_str=response_str,
            ),
            content_type="text/event-stream",
        )

    vector_store_table = chat.options.qa_library.uuid_hex
    model = chat.options.qa_model
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
    if len(data_sources) and len(data_sources) != max_data_sources:
        filters.filters.append(
            MetadataFilter(
                key="data_source_uuid",
                value=[data_source.uuid_hex for data_source in data_sources],
                operator="in",
            )
        )
    llm = OttoLLM(model, 0.1)
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
        if eval:
            return response_str, source_nodes
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat, response_message.id, response_str=response_str
            ),
            content_type="text/event-stream",
        )

    response = synthesizer.synthesize(query=input, nodes=source_nodes)

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            response_generator=response.response_gen,
            source_nodes=response.source_nodes,
            llm=llm,
        ),
        content_type="text/event-stream",
    )


def error_response(chat, response_message):
    """
    Send an error message to the user.
    """
    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            response_str=_("Sorry, this isn't working right now."),
        ),
        content_type="text/event-stream",
    )
