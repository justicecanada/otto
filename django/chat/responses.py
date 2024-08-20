import asyncio
import json

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import HttpResponse, StreamingHttpResponse
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from asgiref.sync import sync_to_async
from langchain.schema import AIMessage, HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from llama_index.core.retrievers import QueryFusionRetriever
from rules.contrib.views import objectgetter
from structlog import get_logger

from chat.models import Message
from chat.tasks import translate_file
from chat.utils import (
    htmx_stream,
    num_tokens_from_string,
    summarize_long_text,
    summarize_long_text_async,
    sync_generator_to_async,
    url_to_text,
)
from librarian.models import DataSource, Document, Library
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

    user_message = response_message.parent

    system_prompt = chat.options.chat_system_prompt
    chat_history = [SystemMessage(content=system_prompt)]
    chat_history += [
        (
            AIMessage(content=message.text)
            if message.is_bot
            else HumanMessage(content=message.text)
        )
        for message in chat.messages.all().order_by("date_created")
    ]

    model = chat.options.chat_model

    tokens = num_tokens_from_string(
        " ".join(message.content for message in chat_history)
    )
    if (tokens > 16384 and model == "gpt-35") or (tokens > 131072):
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

    temperature = chat.options.chat_temperature
    llm = AzureChatOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        azure_deployment=f"{model}",
        model=model,
        api_version=settings.AZURE_OPENAI_VERSION,
        api_key=settings.AZURE_OPENAI_KEY,
        temperature=temperature,
    )

    if eval:
        # Just return the full response and an empty list representing source nodes
        return llm.invoke(chat_history).content, []

    llm_stream = llm.astream(chat_history)
    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            response_stream=llm_stream,
            chunk_property="content",
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

    async def multi_summary_generator():
        for i, file in enumerate(files):
            yield f"**{file.filename}**\n\n"
            if not file.text:
                await sync_to_async(file.extract_text)(fast=True)
            summary = await summarize_long_text_async(
                file.text,
                summary_length,
                target_language,
                custom_summarize_prompt,
                model,
            )

            if i < len(files) - 1:
                yield f"{summary}\n\n-----\n"
            else:
                yield f"{summary}\n<<END>>"

    if len(files) > 0:
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat, response_message.id, response_generator=multi_summary_generator()
            ),
            content_type="text/event-stream",
        )
    elif user_message.text == "":
        summary = "No text to summarize."
    else:
        url_validator = URLValidator()
        try:
            url_validator(user_message.text)
            text_to_summarize = url_to_text(user_message.text)
        except ValidationError:
            logger.error("Invalid URL", url=user_message.text)
            text_to_summarize = user_message.text

        # Check if response text is too short (most likely a website blocking Otto)
        if len(text_to_summarize.split()) < 35:
            summary = _(
                "This website blocks Otto from retrieving text. Try copy & pasting the webpage here."
            )
        else:
            summary = summarize_long_text(
                text_to_summarize,
                summary_length,
                target_language,
                custom_summarize_prompt,
                model,
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
        yield await sync_to_async(file_msg)(response_message, len(files)) + "<<END>>"

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

    gpt35 = AzureChatOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        azure_deployment=settings.DEFAULT_CHAT_MODEL,
        model=settings.DEFAULT_CHAT_MODEL,
        api_version=settings.AZURE_OPENAI_VERSION,
        api_key=settings.AZURE_OPENAI_KEY,
        temperature=0.1,
    )

    # Note that long plain-translations frequently fail due to output token limits
    # It is not easy to check for this in advance, so we just try and see what happens

    llm_stream = gpt35.astream([HumanMessage(content=translate_prompt)])

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            response_stream=llm_stream,
            chunk_property="content",
        ),
        content_type="text/event-stream",
    )


def qa_response(chat, response_message, eval=False):
    """
    Answer the user's question using a specific vector store table.
    """
    from llama_index.core import ServiceContext, VectorStoreIndex
    from llama_index.core.prompts import PromptTemplate, PromptType
    from llama_index.core.query_engine import RetrieverQueryEngine
    from llama_index.core.response_synthesizers import CompactAndRefine
    from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
    from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
    from llama_index.vector_stores.postgres import PGVectorStore

    def get_query_engine(
        vector_store_table,
        filters,
        service_context,
        top_k=5,
        qa_prompt_template="{context}\n{query}",
        vector_weight=0.6,
    ):
        hybrid_vector_store = PGVectorStore.from_params(
            database=settings.DATABASES["vector_db"]["NAME"],
            host=settings.DATABASES["vector_db"]["HOST"],
            password=settings.DATABASES["vector_db"]["PASSWORD"],
            port=5432,
            user=settings.DATABASES["vector_db"]["USER"],
            table_name=vector_store_table,
            embed_dim=1536,  # openai embedding dimension
            hybrid_search=True,
            text_search_config="simple",
            perform_setup=True,
        )

        pg_idx = VectorStoreIndex.from_vector_store(
            vector_store=hybrid_vector_store,
            service_context=service_context,
            show_progress=False,
        )

        response_synthesizer = CompactAndRefine(
            streaming=True,
            service_context=service_context,
            text_qa_template=qa_prompt_template,
        )

        vector_retriever = pg_idx.as_retriever(
            vector_store_query_mode="default",
            similarity_top_k=max(top_k, 100),
            filters=filters,
        )

        # TODO: If we search for "Who does Ebrahim Adeeb report to?", we got results
        # that weren't really relevant. When we asked ChatGPT to recommend retriever
        # weights based on the query, it told us [0.3, 0.7] and those weights did
        # perform better. So we should consider using either an LLM or a custom
        # model to determine the retriever weights (and possibly other settings).

        text_retriever = pg_idx.as_retriever(
            vector_store_query_mode="sparse",
            similarity_top_k=max(top_k, 100),
            filters=filters,
        )
        retriever = QueryFusionRetriever(
            [vector_retriever, text_retriever],
            similarity_top_k=top_k,
            num_queries=1,  # set this to 1 to disable query generation
            mode="relative_score",
            use_async=False,
            retriever_weights=[vector_weight, 1 - vector_weight],
            llm=service_context.llm,
        )

        query_engine = RetrieverQueryEngine(
            retriever=retriever,
            response_synthesizer=response_synthesizer,
        )

        return query_engine

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
    llm = AzureChatOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        azure_deployment=f"{model}",
        model=model,
        api_version=settings.AZURE_OPENAI_VERSION,
        api_key=settings.AZURE_OPENAI_KEY,
        temperature=0,
    )
    embed_model = AzureOpenAIEmbedding(
        model="text-embedding-3-large",
        deployment_name="text-embedding-3-large",
        dimensions=1536,
        embed_batch_size=16,
        api_key=settings.AZURE_OPENAI_KEY,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_version=settings.AZURE_OPENAI_VERSION,
    )
    service_context = ServiceContext.from_defaults(
        llm=llm,
        embed_model=embed_model,
    )
    query_engine = get_query_engine(
        vector_store_table,
        filters,
        service_context,
        top_k,
        chat.options.qa_prompt_combined,
        chat.options.qa_vector_ratio,
    )
    input = response_message.parent.text

    response = query_engine.query(input)
    if len(response.source_nodes) == 0:
        response_str = _(
            "Sorry, I couldn't find any information about that. Try selecting a different library or data source."
        )
        if eval:
            return response_str, response.source_nodes
        return StreamingHttpResponse(
            streaming_content=htmx_stream(
                chat, response_message.id, response_str=response_str
            ),
            content_type="text/event-stream",
        )
    elif eval:
        return response.response, response.source_nodes

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            response_stream=sync_generator_to_async(response.response_gen),
            source_nodes=response.source_nodes,
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
