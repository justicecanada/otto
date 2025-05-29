import urllib.parse
import uuid

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

import markdown
from llama_index.core import ChatPromptTemplate
from llama_index.core.llms import ChatMessage, MessageRole
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from chat.llm import OttoLLM
from otto.models import OttoStatus
from otto.utils.decorators import app_access_required, budget_required

from .forms import LawSearchForm
from .models import Law
from .prompts import (
    default_additional_instructions,
    qa_prompt_instruction_tmpl,
    system_prompt_tmpl,
)
from .utils import (
    get_law_url,
    get_other_lang_node,
    get_source_node,
    htmx_sse_error,
    htmx_sse_response,
    num_tokens,
)

TEXT_QA_SYSTEM_PROMPT = ChatMessage(
    content=system_prompt_tmpl,
    role=MessageRole.SYSTEM,
)
TEXT_QA_PROMPT_TMPL_MSGS = [
    TEXT_QA_SYSTEM_PROMPT,
    ChatMessage(
        content=qa_prompt_instruction_tmpl,
        role=MessageRole.USER,
    ),
]


logger = get_logger(__name__)

md = markdown.Markdown(extensions=["fenced_code", "nl2br", "tables"], tab_length=2)

app_name = "laws"


@app_access_required(app_name)
def index(request):
    context = {
        "hide_breadcrumbs": True,
        "form": LawSearchForm(),
        "last_updated": OttoStatus.objects.singleton().laws_last_refreshed,
    }
    return render(request, "laws/laws.html", context=context)


def source(request, source_id):
    source_id = urllib.parse.unquote_plus(source_id)
    source_node = get_source_node(source_id)
    # What language is the source_node?
    lang = "eng" if "eng" in source_node["metadata"]["doc_id"] else "fra"
    if lang == "eng":
        law = Law.objects.filter(node_id_en=source_node["metadata"]["doc_id"]).first()
    else:
        law = Law.objects.filter(node_id_fr=source_node["metadata"]["doc_id"]).first()
    other_lang_node = get_other_lang_node(source_id)
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
    law.url = get_law_url(law, lang)
    context = {
        "source_node": source_node,
        "other_lang_node": other_lang_node,
        "law": law,
    }
    if not source_node:
        return HttpResponse(_("Source not found."), status=404)

    return render(request, "laws/source_details.html", context=context)


@app_access_required(app_name)
@budget_required
def answer(request, query_uuid):
    bind_contextvars(feature="laws_query")
    from llama_index.core.schema import MetadataMode

    query_info = cache.get(query_uuid)
    if not query_info:
        return StreamingHttpResponse(
            streaming_content=htmx_sse_error(
                "query uuid not found in cache", query_uuid
            ),
            content_type="text/event-stream",
        )

    additional_instructions = query_info["additional_instructions"]
    # unquote_plus the instructions so they can be passed to the LLM
    additional_instructions = urllib.parse.unquote_plus(additional_instructions)
    CHAT_TEXT_QA_PROMPT = ChatPromptTemplate(
        message_templates=TEXT_QA_PROMPT_TMPL_MSGS
    ).partial_format(additional_instructions=additional_instructions)

    sources = query_info["sources"]
    query = query_info["query"]
    trim_redundant = query_info["trim_redundant"]
    model = query_info["model"]
    max_tokens = query_info["context_tokens"]

    llm = OttoLLM(deployment=model, temperature=0)
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
                        parent_tokens = num_tokens(
                            sources[parent_index].node.get_content(
                                metadata_mode=MetadataMode.LLM
                            ),
                            "gpt-4o",
                        )
                        if total_tokens + parent_tokens <= max_tokens:
                            source = sources.pop(parent_index)
            source_tokens = num_tokens(
                source.node.get_content(metadata_mode=MetadataMode.LLM),
                "gpt-4o",
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

        response_synthesizer = llm.get_response_synthesizer(CHAT_TEXT_QA_PROMPT)
        cache.delete(f"sources_{query}")

        streaming_response = response_synthesizer.synthesize(
            query=query,
            nodes=sources,
        )
        generator = streaming_response.response_gen

    return StreamingHttpResponse(
        streaming_content=htmx_sse_response(generator, llm, query_uuid),
        content_type="text/event-stream",
    )


@app_access_required(app_name)
def existing_search(request, query_uuid):
    """For back/forward navigation or (short-term) sharing of a search result page."""
    query_info = cache.get(query_uuid)
    if not query_info:
        return redirect("laws:index")
    context = {
        "form": LawSearchForm(),
        "hide_breadcrumbs": True,
        "sources": sources_to_html(query_info["sources"]),
        "query": query_info["query"],
        "query_uuid": query_uuid,
        "answer": query_info.get("answer", None),
    }
    return render(request, "laws/laws.html", context=context)


@app_access_required(app_name)
@budget_required
def search(request):
    bind_contextvars(feature="laws_query")
    if request.method != "POST":
        return redirect("laws:index")
    try:
        from langdetect import detect
        from llama_index.core.retrievers import QueryFusionRetriever
        from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters

        llm = OttoLLM()

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
        # Trim query to 5000 characters
        query_too_long = len(query) > 5000
        if query_too_long:
            query = query[:5000] + "..."
        query_too_long_for_keyword_search = len(query) > 200

        pg_idx = llm.get_index("laws_lois__", hnsw=True)

        advanced_mode = request.POST.get("advanced") == "true"
        disable_llm = not (request.POST.get("ai_answer", False) == "on")
        detect_lang = not (request.POST.get("bilingual_results", None) == "on")
        selected_laws = Law.objects.all()
        trim_redundant = False

        logger.info(
            "Law search query",
            query=query,
            advanced_mode=advanced_mode,
            disable_llm=disable_llm,
            detect_lang=detect_lang,
            pg_idx=pg_idx,
        )

        if not advanced_mode:
            vector_ratio = 0.8
            top_k = 25
            # Options for the AI answer
            model = settings.DEFAULT_LAWS_MODEL
            context_tokens = 5000
            # Cast to string evaluates the lazy translation
            additional_instructions = str(default_additional_instructions)
            additional_instructions = urllib.parse.quote_plus(additional_instructions)
        else:
            vector_ratio = float(request.POST.get("vector_ratio", 0.8))
            top_k = int(request.POST.get("top_k", 25))
            # trim_redundant = request.POST.get("trim_redundant", "on") == "on"
            model = request.POST.get("model", settings.DEFAULT_LAWS_MODEL)
            context_tokens = int(request.POST.get("context_tokens", 5000))
            additional_instructions = request.POST.get("additional_instructions", "")
            # Need to escape the instructions so they can be passed in GET parameter
            additional_instructions = urllib.parse.quote_plus(additional_instructions)
            # Whether to select individual documents depends on "search_laws_option"
            search_laws_option = request.POST.get("search_laws_option", "all")
            if search_laws_option == "acts":
                selected_laws = Law.objects.filter(type="act")
            elif search_laws_option == "regulations":
                selected_laws = Law.objects.filter(type="regulation")
            elif search_laws_option == "specific_laws":
                laws = request.POST.getlist("laws")
                selected_laws = Law.objects.filter(pk__in=laws)
            elif search_laws_option == "enabling_acts":
                enabling_acts = request.POST.getlist("enabling_acts")
                enabling_acts = Law.objects.filter(pk__in=enabling_acts)
                selected_laws = Law.objects.filter(
                    enabling_authority_en__in=[
                        enabling_acts.ref_number_en for enabling_acts in enabling_acts
                    ]
                )
        if detect_lang:
            # Detect the language of the query and search only documents in that lang
            lang = detect(query)
            if lang not in ["en", "fr"]:
                lang = request.LANGUAGE_CODE
            lang = "eng" if lang == "en" else "fra"
            if lang == "fra":
                doc_id_list = [law.node_id_fr for law in selected_laws]
            else:
                doc_id_list = [law.node_id_en for law in selected_laws]
        else:
            doc_id_list = [law.node_id_en for law in selected_laws] + [
                law.node_id_fr for law in selected_laws
            ]

        filters.append(
            MetadataFilter(
                key="doc_id",
                value=doc_id_list,
                operator="in",
            )
        )

        if request.POST.get("date_filter_option", "all") != "all":
            in_force_date_start = request.POST.get("in_force_date_start", None)
            in_force_date_end = request.POST.get("in_force_date_end", None)
            last_amended_date_start = request.POST.get("last_amended_date_start", None)
            last_amended_date_end = request.POST.get("last_amended_date_end", None)
            if in_force_date_start:
                filters.append(
                    MetadataFilter(
                        key="in_force_start_date",
                        value=in_force_date_start,
                        operator=">=",
                    )
                )
            if in_force_date_end:
                filters.append(
                    MetadataFilter(
                        key="in_force_start_date",
                        value=in_force_date_end,
                        operator="<=",
                    )
                )
            if last_amended_date_start:
                filters.append(
                    MetadataFilter(
                        key="last_amended_date",
                        value=last_amended_date_start,
                        operator=">=",
                    )
                )
            if last_amended_date_end:
                filters.append(
                    MetadataFilter(
                        key="last_amended_date",
                        value=last_amended_date_end,
                        operator="<=",
                    )
                )

        if query_too_long_for_keyword_search:
            vector_ratio = 1

        filters = MetadataFilters(filters=filters)
        if vector_ratio == 1:
            retriever = pg_idx.as_retriever(
                vector_store_query_mode="default",
                similarity_top_k=top_k,
                filters=filters,
                vector_store_kwargs={"hnsw_ef_search": 256},
            )
        elif vector_ratio == 0:
            retriever = pg_idx.as_retriever(
                vector_store_query_mode="sparse",
                similarity_top_k=top_k,
                filters=filters,
            )
            retriever._vector_store.is_embedding_query = False
        else:
            vector_retriever = pg_idx.as_retriever(
                vector_store_query_mode="default",
                similarity_top_k=max(top_k * 2, 100),
                filters=filters,
                vector_store_kwargs={"hnsw_ef_search": 256},
            )
            # Need to create a separate OttoLLM instance so that we can
            # set is_embedding_query to False for the text retriever.
            # This is a hack for LlamaIndex PGVectorStore implementation
            # because otherwise it would embed the query even though
            # we are just doing a postgres text search.
            pg_idx2 = OttoLLM().get_index("laws_lois__", hnsw=True)
            text_retriever = pg_idx2.as_retriever(
                vector_store_query_mode="sparse",
                similarity_top_k=max(top_k * 2, 100),
                filters=filters,
            )
            text_retriever._vector_store.is_embedding_query = False

            retriever = QueryFusionRetriever(
                retrievers=[vector_retriever, text_retriever],
                mode="relative_score",
                llm=llm.llm,
                similarity_top_k=top_k,
                num_queries=1,
                use_async=False,
                retriever_weights=[vector_ratio, 1 - vector_ratio],
            )

        try:
            sources = retriever.retrieve(query)
        except:
            sources = None
        llm.create_costs()
        if not sources:
            context = {
                "sources": [],
                "query": query,
                "disable_llm": True,
                "answer_params": "",
            }
            return render(request, "laws/search_result.html", context=context)

        # Cache sources so they can be retrieved in the AI answer function
        query_uuid = uuid.uuid4()
        query_info = {
            "sources": sources,
            "query": query,
            "trim_redundant": trim_redundant,
            "model": model,
            "context_tokens": context_tokens,
            "additional_instructions": additional_instructions,
        }
        cache.set(query_uuid, query_info, timeout=300)

        context = {
            "sources": sources_to_html(sources),
            "query": query,
            "query_uuid": query_uuid,
            "disable_llm": disable_llm,
        }
    except Exception as e:
        context = {
            "sources": [],
            "query": query,
            "query_uuid": query_uuid,
            "disable_llm": True,
            "error": e,
        }
        logger.exception(
            f"Error in laws search for query: {query}",
            query_uuid=query_uuid,
            error=e,
        )

        return render(request, "laws/search_result.html", context=context)

    response = render(
        request,
        "laws/search_result.html",
        context=context,
    )

    new_url = reverse("laws:existing_search", args=[str(query_uuid)])
    response["HX-Push-Url"] = new_url

    return response


def sources_to_html(sources):
    return [
        {
            "node_id": urllib.parse.quote_plus(s.node.node_id).replace("+", "-"),
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
    ]
