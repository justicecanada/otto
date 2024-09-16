import urllib.parse

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

import markdown
from llama_index.core import ChatPromptTemplate
from llama_index.core.llms import ChatMessage, MessageRole
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from chat.llm import OttoLLM
from otto.utils.decorators import app_access_required

from .forms import LawSearchForm
from .models import Law
from .prompts import qa_prompt_instruction_tmpl, system_prompt
from .utils import (
    get_law_url,
    get_other_lang_node,
    get_source_node,
    htmx_sse_response,
    num_tokens,
)

TEXT_QA_SYSTEM_PROMPT = ChatMessage(
    content=system_prompt,
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
    form = LawSearchForm()
    context = {"hide_breadcrumbs": True, "form": form}
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
    law.url = get_law_url(law, request.LANGUAGE_CODE)
    context = {
        "source_node": source_node,
        "other_lang_node": other_lang_node,
        "law": law,
    }
    if not source_node:
        return HttpResponse(_("Source not found."), status=404)

    return render(request, "laws/source_details.html", context=context)


@app_access_required(app_name)
def answer(request):
    bind_contextvars(feature="laws_query")
    from llama_index.core.schema import MetadataMode

    additional_instructions = request.GET.get("additional_instructions", "")

    CHAT_TEXT_QA_PROMPT = ChatPromptTemplate(
        message_templates=TEXT_QA_PROMPT_TMPL_MSGS
    ).partial_format(additional_instructions=additional_instructions)

    # from llama_index.llms.groq import Groq

    model = request.GET.get("model", settings.DEFAULT_CHAT_MODEL)
    max_tokens = int(request.GET.get("context_tokens", 2000))
    trim_redundant = bool(request.GET.get("trim_redundant", False))
    query = urllib.parse.unquote_plus(request.GET.get("query", ""))
    logger.debug(query)

    llm = OttoLLM(deployment=model, temperature=0)
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
                        parent_tokens = num_tokens(
                            sources[parent_index].node.get_content(
                                metadata_mode=MetadataMode.LLM
                            ),
                            "gpt-4-turbo-preview",
                        )
                        if total_tokens + parent_tokens <= max_tokens:
                            source = sources.pop(parent_index)
            source_tokens = num_tokens(
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

        response_synthesizer = llm.get_response_synthesizer(CHAT_TEXT_QA_PROMPT)
        cache.delete(f"sources_{query}")

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
        generator = streaming_response.response_gen

    return StreamingHttpResponse(
        streaming_content=htmx_sse_response(generator, query, llm),
        content_type="text/event-stream",
    )


@app_access_required(app_name)
def search(request):
    bind_contextvars(feature="laws_query")
    if request.method != "POST":
        # redirect to laws index
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
        pg_idx = llm.get_index("laws_lois__", hnsw=True)

        advanced_mode = request.POST.get("advanced") == "true"
        disable_llm = not (request.POST.get("ai_answer", False) == "on")
        detect_lang = not (request.POST.get("bilingual_results", None) == "on")
        selected_laws = Law.objects.all()

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
            model = "gpt-4o"
            context_tokens = 2000
            additional_instructions = ""
        else:
            vector_ratio = float(request.POST.get("vector_ratio", 1))
            top_k = int(request.POST.get("top_k", 25))
            trim_redundant = request.POST.get("trim_redundant", "on") == "on"
            model = request.POST.get("model", settings.DEFAULT_CHAT_MODEL)
            context_tokens = request.POST.get("context_tokens", 2000)
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
                vector_store_query_mode="sparse",
                similarity_top_k=top_k,
                filters=filters,
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
        if additional_instructions:
            answer_params += f"&additional_instructions={additional_instructions}"

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
    except Exception as e:
        context = {
            "sources": [],
            "query": query,
            "disable_llm": True,
            "answer_params": "",
            "error": e,
        }
        return render(request, "laws/search_result.html", context=context)

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
