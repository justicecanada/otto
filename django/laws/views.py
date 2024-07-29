import time
import urllib.parse

from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

import markdown
import tiktoken
from rules.contrib.views import permission_required
from structlog import get_logger

from chat.utils import llm_response_to_html, sync_generator_to_async
from otto.utils.decorators import app_access_required, permission_required

from .models import Law

logger = get_logger(__name__)

md = markdown.Markdown(extensions=["fenced_code", "nl2br", "tables"], tab_length=2)

app_name = "laws"


def _num_tokens(string: str, model_name: str) -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.encoding_for_model(model_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens


def _get_source_node(node_id):
    # Get the source node from the database (settings.DATABASES["vector_db"])
    # It is in a table data_laws_lois__ and column is "node_id"
    # Return the "text" and "metadata_" columns
    # "metadata_" column is a JSON field, so you can access it like a dictionary
    with connections["vector_db"].cursor() as cursor:
        cursor.execute(
            f"SELECT text, metadata_ FROM data_laws_lois__ WHERE node_id = '{node_id}'"
        )
        row = cursor.fetchone()
        if row:
            return {
                "text": row[0],
                "metadata": row[1],
            }
    return None


def _get_other_lang_node(node_id):
    # Replace "eng" with "fra" and vice versa
    lang = "eng" if "eng" in node_id else "fra"
    other_lang_node_id = (
        node_id.replace("eng", "fra")
        if lang == "eng"
        else node_id.replace("fra", "eng")
    )
    return _get_source_node(other_lang_node_id)


def _get_law_url(law):
    ref = law.ref_number.replace(" ", "-").replace("/", "-")
    # Constitution has special case
    if ref == "Const" and law.lang == "eng":
        return "https://laws-lois.justice.gc.ca/eng/Const/Const_index.html"
    if ref == "Const" and law.lang == "fra":
        return "https://laws-lois.justice.gc.ca/fra/ConstRpt/Const_index.html"
    if law.type == "act" and law.lang == "eng":
        return f"https://laws-lois.justice.gc.ca/eng/acts/{ref}/"
    if law.type == "act" and law.lang == "fra":
        return f"https://laws-lois.justice.gc.ca/fra/lois/{ref}/"
    if law.type == "regulation" and law.lang == "eng":
        return f"https://laws-lois.justice.gc.ca/eng/regulations/{ref}/"
    if law.type == "regulation" and law.lang == "fra":
        return f"https://laws-lois.justice.gc.ca/fra/reglements/{ref}/"


@app_access_required(app_name)
def index(request):
    context = {"hide_breadcrumbs": True}
    return render(request, "laws/laws.html", context=context)


def source(request, source_id):
    source_id = urllib.parse.unquote_plus(source_id)
    source_node = _get_source_node(source_id)
    other_lang_node = _get_other_lang_node(source_id)
    law = Law.objects.filter(node_id=source_node["metadata"]["doc_id"]).first()
    nodes = [source_node, other_lang_node]
    if other_lang_node is None:
        nodes = [source_node]
    for node in nodes:
        node["title"] = node["metadata"]["display_metadata"].split("\n")[0]
        node["html"] = md.convert(node["text"])
        node["headings"] = node["metadata"].get("headings", None)
        node["chunk"] = (
            node["metadata"]["chunk"]
            if not node["metadata"]["chunk"].endswith("/1")
            else None
        )
    law.url = _get_law_url(law)
    context = {
        "source_node": source_node,
        "other_lang_node": other_lang_node,
        "law": law,
    }
    if not source_node:
        return HttpResponse(_("Source not found."), status=404)

    return render(request, "laws/source_details.html", context=context)


def advanced_search_form(request):
    """
    Returns the search form HTML for advanced search.
    Advanced search requires some database queries to populate the form.
    """
    context = {}
    context["documents"] = Law.objects.all().order_by("title")
    for document in context["documents"]:
        if ": " in document.title:
            document.title = (
                f'{document.title.split(": ")[0]} ({document.title.split("(")[-1]}'
            )
    context["act_count"] = Law.objects.filter(type="act").count()
    context["reg_count"] = Law.objects.filter(type="regulation").count()
    context["model_options"] = [
        {"id": "gpt-4", "name": "GPT-4"},
        {"id": "gpt-35-turbo", "name": "GPT-3.5"},
    ]
    # if settings.GROQ_API_KEY:
    #     context["model_options"] += [
    #         {"id": "llama3-70b-8192", "name": "Llama3 70B (Groq)"},
    #         {"id": "llama3-8b-8192", "name": "Llama3 8B (Groq)"},
    #     ]

    return render(request, f"laws/advanced_search_form.html", context=context)


@app_access_required(app_name)
def answer(request):
    from llama_index.core.response_synthesizers import CompactAndRefine
    from llama_index.core.schema import MetadataMode
    from llama_index.core.service_context import ServiceContext
    from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
    from llama_index.llms.azure_openai import AzureOpenAI

    # from llama_index.llms.groq import Groq

    model = request.GET.get("model", "gpt-4")
    max_tokens = int(request.GET.get("context_tokens", 2000))
    trim_redundant = bool(request.GET.get("trim_redundant", False))
    query = urllib.parse.unquote_plus(request.GET.get("query", ""))
    logger.debug(query)

    # if "llama" in model:
    #     llm = Groq(model=model, api_key=settings.GROQ_API_KEY, temperature=0.1)
    # else:
    llm = AzureOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        azure_deployment=f"{model}-unfiltered",
        model="gpt-4-turbo-preview" if model == "gpt-4" else model,
        api_version=settings.AZURE_OPENAI_VERSION,
        api_key=settings.AZURE_OPENAI_KEY,
        temperature=0.1,
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
    sources = cache.get(f"sources_{query}")
    if not sources:
        generator = iter([_("Error generating AI response.")])
    else:
        trimmed_sources = []
        total_tokens = 0

        if trim_redundant:
            source_section_ids = set(
                [source.node.metadata["section_id"] for source in sources]
            )
        added_ids = set()

        # Allow up to 4000 tokens of context.
        while sources:
            source = sources.pop(0)
            if trim_redundant:
                # Check if source has a parent node in "sources" list
                if source.node.metadata["parent_id"] in added_ids:
                    continue
                elif source.node.metadata["parent_id"] in source_section_ids:
                    # Find the parent node in the sources list, remove it, and make it the "source"
                    parent_index = next(
                        (
                            i
                            for i, s in enumerate(sources)
                            if s.node.metadata["section_id"]
                            == source.node.metadata["parent_id"]
                        ),
                        None,
                    )
                    if parent_index:
                        parent_tokens = _num_tokens(
                            sources[parent_index].node.get_content(
                                metadata_mode=MetadataMode.LLM
                            ),
                            "gpt-4-turbo-preview",
                        )
                        if total_tokens + parent_tokens <= max_tokens:
                            source = sources.pop(parent_index)
            source_tokens = _num_tokens(
                source.node.get_content(metadata_mode=MetadataMode.LLM),
                "gpt-4-turbo-preview",
            )
            if total_tokens + source_tokens <= max_tokens:
                trimmed_sources.append(source)
                added_ids.add(source.node.metadata["section_id"])
                total_tokens += source_tokens
            else:
                continue
        logger.debug("\n\n\nSources passed to LLM:")
        for source in trimmed_sources:
            logger.debug(source.node.metadata["display_metadata"])
            logger.debug(f'Section ID: {source.node.metadata["section_id"]}')
            logger.debug(f'Parent ID: {source.node.metadata["parent_id"]}')
        sources = trimmed_sources
        logger.debug("\n\n\n")

        response_synthesizer = CompactAndRefine(
            service_context=service_context, streaming=True
        )
        query_suffix = (
            "\nRespond in markdown format. "
            "The most important words should be **bolded like this**."
            "Answer the query directly if possible. Do not refer to sections or subsections "
            "unnecessarily; instead, provide the answer directly."
        )
        streaming_response = response_synthesizer.synthesize(
            query=query + query_suffix,
            nodes=sources,
        )
        cache.delete(f"sources_{query}")
        generator = streaming_response.response_gen

    def htmx_sse_response(response_gen):
        # time.sleep(60)
        sse_joiner = "\ndata: "
        full_message = ""
        message_html_lines = []
        try:
            for text in response_gen:
                full_message += text
                # When message has uneven # of '```' then add a closing '```' on a newline
                tmp_full_message = full_message
                if full_message.count("```") % 2 == 1:
                    tmp_full_message = full_message + "\n```"
                # Parse Markdown of full_message to HTML
                message_html = llm_response_to_html(tmp_full_message)
                message_html_lines = message_html.split("\n")
                if len(full_message) > 1:
                    yield (
                        f"data: <div>{sse_joiner.join(message_html_lines)}</div>\n\n"
                    )
        except Exception as e:
            error = str(e)
            full_message = _("An error occurred:") + f"\n```\n{error}\n```"
            message_html = llm_response_to_html(full_message)
            message_html_lines = message_html.split("\n")

        yield (
            f"data: <div hx-swap-oob='true' id='answer-sse'>"
            f"<div>{sse_joiner.join(message_html_lines)}</div></div>\n\n"
        )

    return StreamingHttpResponse(
        streaming_content=sync_generator_to_async(htmx_sse_response(generator)),
        content_type="text/event-stream",
    )


@app_access_required(app_name)
def search(request):
    if request.method != "POST":
        # redirect to laws index
        return redirect("laws:index")
    from langdetect import detect
    from llama_index.core.retrievers import QueryFusionRetriever
    from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
    from llama_index.llms.azure_openai import AzureOpenAI

    # time.sleep(60)
    # We don't want to search Document nodes - only chunks
    filters = [
        MetadataFilter(
            key="node_type",
            value="chunk",
            operator="==",
        ),
    ]

    query = request.POST.get("query")
    pg_idx = Law.get_index()

    advanced_mode = request.POST.get("advanced") == "true"
    disable_llm = not (request.POST.get("ai_answer", False) == "on")
    detect_lang = not (request.POST.get("bilingual_results", None) == "on")
    doc_id_list = None

    logger.info(
        "Search query",
        query=query,
        advanced_mode=advanced_mode,
        disable_llm=disable_llm,
        detect_lang=detect_lang,
        pg_idx=pg_idx,
    )

    if not advanced_mode:
        vector_ratio = 1
        top_k = 25
        # Options for the AI answer
        trim_redundant = True
        model = "gpt-4"
        context_tokens = 2000
    else:
        vector_ratio = float(request.POST.get("vector_ratio", 1))
        top_k = int(request.POST.get("top_k", 25))
        trim_redundant = request.POST.get("trim_redundant", "on") == "on"
        model = request.POST.get("model", "gpt-4")
        context_tokens = request.POST.get("context_tokens", 2000)

        # Search only the selected documents
        doc_id_list = request.POST.getlist("acts") + request.POST.getlist("regs")

    if detect_lang and not advanced_mode:
        # Detect the language of the query and search only documents in that lang
        lang = detect(query).replace("en", "eng").replace("fr", "fra")
        if lang in ["eng", "fra"]:
            if doc_id_list is None:
                doc_id_list = [law.node_id for law in Law.objects.filter(lang=lang)]
            else:
                doc_id_list = [
                    law.node_id
                    for law in Law.objects.filter(lang=lang)
                    if law.node_id in doc_id_list
                ]

    if doc_id_list is not None:
        filters.append(
            MetadataFilter(
                key="doc_id",
                value=doc_id_list,
                operator="in",
            )
        )
    filters = MetadataFilters(filters=filters)
    if vector_ratio == 1:
        retriever = pg_idx.as_retriever(
            vector_store_query_mode="default",
            similarity_top_k=top_k,
            filters=filters,
            vector_store_kwargs={"hnsw_ef_search": 300},
        )
    elif vector_ratio == 0:
        retriever = pg_idx.as_retriever(
            vector_store_query_mode="sparse", similarity_top_k=top_k, filters=filters
        )
    else:
        vector_retriever = pg_idx.as_retriever(
            vector_store_query_mode="default",
            similarity_top_k=max(top_k * 2, 100),
            filters=filters,
            vector_store_kwargs={"hnsw_ef_search": 300},
        )
        text_retriever = pg_idx.as_retriever(
            vector_store_query_mode="sparse",
            similarity_top_k=max(top_k * 2, 100),
            filters=filters,
        )
        retriever = QueryFusionRetriever(
            retrievers=[vector_retriever, text_retriever],
            mode="relative_score",
            llm=AzureOpenAI(
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                azure_deployment=f"gpt-4-unfiltered",
                model="gpt-4-turbo-preview",
                api_version=settings.AZURE_OPENAI_VERSION,
                api_key=settings.AZURE_OPENAI_KEY,
                temperature=0.1,
            ),
            similarity_top_k=top_k,
            num_queries=1,
            use_async=False,
            retriever_weights=[vector_ratio, 1 - vector_ratio],
        )

    if advanced_mode and len(doc_id_list) == 0:
        context = {
            "sources": [],
            "query": query,
            "disable_llm": True,
            "answer_params": "",
        }
        return render(request, "laws/search_result.html", context=context)
    sources = retriever.retrieve(query)

    # Cache sources so they can be retrieved in the AI answer function
    cache.set(f"sources_{query}", sources, timeout=60)

    # Pass options through to the AI answer function
    url_encoded_query = urllib.parse.quote_plus(query)
    answer_params = f"?query={url_encoded_query}"
    if trim_redundant:
        answer_params += f"&trim_redundant={trim_redundant}"
    if model:
        answer_params += f"&model={model}"
    if context_tokens:
        answer_params += f"&context_tokens={context_tokens}"

    context = {
        "sources": [
            {
                "node_id": urllib.parse.quote_plus(s.node.node_id),
                "title": s.node.metadata["display_metadata"].split("\n")[0],
                "chunk": (
                    s.node.metadata["chunk"]
                    if not s.node.metadata["chunk"].endswith("/1")
                    else None
                ),
                "headings": s.node.metadata.get("headings", None),
                "html": md.convert(s.node.text),
            }
            for s in sources
        ],
        "query": query,
        "disable_llm": disable_llm,
        "answer_params": answer_params,
    }
    response = render(
        request,
        "laws/search_result.html",
        context=context,
    )
    # URL-encode query and append to history URL
    # history_url = "/laws/search/"
    # history_url += f"?query={urllib.parse.quote_plus(query)}"
    # response["HX-Push-Url"] = history_url
    return response
