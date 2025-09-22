import time
import urllib.parse
import uuid

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

import markdown
from llama_index.core import ChatPromptTemplate
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
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
from .search_history.models import LawSearch
from .utils import (
    get_display_title,
    get_law_url,
    get_other_lang_node,
    get_source_node,
    html_render,
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
        "active_app": "laws",
        "hide_breadcrumbs": True,
        "form": LawSearchForm(),
        "last_updated": OttoStatus.objects.singleton().laws_last_refreshed,
    }
    return render(request, "laws/laws.html", context=context)


def source(request, source_id):
    source_id = urllib.parse.unquote_plus(source_id)
    source_node = get_source_node(source_id.replace("Constitution-", "Constitution "))
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
        node["title"] = get_display_title(node["metadata"])
        node["html"] = md.convert(node["text"])
        node["headings"] = node["metadata"].get("headings", None)
        node["chunk"] = (
            node["metadata"]["chunk"]
            if not node["metadata"]["chunk"].endswith("/1")
            else None
        )
    law.url = get_law_url(law, lang)

    if law.short_title_en == "THE CONSTITUTION ACTS, 1867 to 1982":
        url_suffix = ""
    else:
        url_suffix = f"{'FullText' if lang=='eng' else 'TexteComplet'}.html#{source_node['metadata']['lims_id']}"
    context = {
        "source_node": source_node,
        "other_lang_node": other_lang_node,
        "law": law,
        "url_suffix": url_suffix,
    }
    if not source_node:
        return HttpResponse(_("Source not found."), status=404)

    return render(request, "laws/source_details.html", context=context)


@app_access_required(app_name)
def get_answer_column(request, query_uuid):
    """Renders the answer column partial."""
    context = {"query_uuid": query_uuid}
    return render(request, "laws/_answer_column.html", context)


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
        logger.info("\n\n\nSources passed to LLM:")
        for source in trimmed_sources:
            logger.info(source.node.metadata["display_metadata"])
            logger.info(f'Section ID: {source.node.metadata["section_id"]}')
            logger.info(f'Parent ID: {source.node.metadata["parent_id"]}')
        sources = trimmed_sources
        logger.info("\n\n\n")

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
@budget_required
def search(request, law_search=None):
    bind_contextvars(feature="laws_query")
    query_uuid = None
    if request.method != "POST":
        return redirect("laws:index")
    try:
        llm = OttoLLM()
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

        advanced_mode = request.POST.get("advanced") == "true"
        disable_llm = not (request.POST.get("ai_answer", False) == "on")
        detect_lang = request.POST.get("detect_language", None) == "on"
        selected_laws = Law.objects.all()
        trim_redundant = False

        logger.info(
            "Law search query",
            query=query,
            advanced_mode=advanced_mode,
            disable_llm=disable_llm,
            detect_lang=detect_lang,
        )

        if not advanced_mode:
            vector_ratio = 0.8
            top_k = 25
            # Options for the AI answer
            model = settings.DEFAULT_LAWS_MODEL
            context_tokens = 100000
            # Cast to string evaluates the lazy translation
            additional_instructions = str(default_additional_instructions)
            additional_instructions = urllib.parse.quote_plus(additional_instructions)
        else:
            vector_ratio = float(request.POST.get("vector_ratio", 0.8))
            top_k = int(request.POST.get("top_k", 25))
            # trim_redundant = request.POST.get("trim_redundant", "on") == "on"
            model = request.POST.get("model", settings.DEFAULT_LAWS_MODEL)
            context_tokens = int(request.POST.get("context_tokens", 100000))
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
        lang = request.POST.get("language", "all")
        if lang != "all" or detect_lang:
            if detect_lang:
                # Detect the language of the query and search only documents in that lang
                # try:
                #     start_time = time.time()
                #     lang = detect(query)
                #     end_time = time.time()
                #     logger.info(
                #         "Detected language for query",
                #         query=query,
                #         lang=lang,
                #         duration=end_time - start_time,
                #     )
                # except Exception as e:
                #     logger.exception(
                #         "Error detecting language for query",
                #         query=query,
                #         error=e,
                #     )
                #     lang = request.LANGUAGE_CODE
                if lang not in ["en", "fr"]:
                    lang = request.LANGUAGE_CODE
            lang = "eng" if lang == "en" else "fra"
            if lang == "fra":
                doc_id_list = [law.node_id_fr for law in selected_laws]
            else:
                doc_id_list = [law.node_id_en for law in selected_laws]
            filters.append(
                MetadataFilter(
                    key="lang",
                    value=lang,
                    operator="==",
                )
            )
        else:
            doc_id_list = [law.node_id_en for law in selected_laws] + [
                law.node_id_fr for law in selected_laws
            ]

        # Only add doc_id filter if needed
        if doc_id_list and selected_laws.count() < Law.objects.count():
            filters.append(
                MetadataFilter(
                    key="doc_id",
                    value=doc_id_list,
                    operator="in",
                )
            )
        elif not doc_id_list:
            # If doc_id_list is empty, return no sources immediately
            context = {
                "sources": [],
                "query": query,
                "query_uuid": None,
                "disable_llm": True,
                "answer_params": "",
            }
            logger.info(
                "No sources found for query (empty doc_id_list)",
                query=query,
            )
            return render(request, "laws/search_result.html", context=context)

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
        retriever = llm.get_retriever(
            vector_store_table="laws_lois__",
            filters=filters,
            # fetch enough docs to examine double the requested top_k for suggestions
            top_k=max(2 * top_k, 100),
            vector_weight=vector_ratio,
            hnsw=True,
        )

        try:
            sources = retriever.retrieve(query)
        except Exception as e:
            logger.exception(
                f"Error retrieving sources for query: {query}",
                query_uuid=query_uuid,
                error=e,
            )
            sources = None
        llm.create_costs()
        if not sources:
            context = {
                "sources": [],
                "query": query,
                "query_uuid": None,
                "disable_llm": True,
                "answer_params": "",
            }
            logger.info(
                "No sources found for query",
                query=query,
            )
            return render(request, "laws/search_result.html", context=context)

        else:
            # collect scores for suggestion and sparkline
            source_scores = [s.score for s in sources if hasattr(s, "score")]
            # compute suggestion: compare next phase (double top_k) to first phase min score
            # compute suggestion: compare next phase (double top_k) to first phase min score
            top_score = source_scores[0] if source_scores else 0
            # Only compute second phase max if enough scores are available
            if len(source_scores) > top_k:
                second_phase_max = source_scores[top_k]
                suggest_increase_results = second_phase_max >= top_score * 0.5
            else:
                suggest_increase_results = False
            # sparkline data: up to double the requested top_k
            sparkline_scores = source_scores[: 2 * top_k]
            # convert scores to pixel heights (max height 20px)
            sparkline_heights = [int(s * 20) for s in sparkline_scores]
            # now limit to the actual top_k sources for display
            sources = sources[:top_k]

        # Create LawSearch object for authenticated users, unless replaying history
        if request.user.is_authenticated and not getattr(
            request, "_from_history", False
        ):
            # Collect search parameters
            search_parameters = {
                "advanced": advanced_mode,
                "ai_answer": request.POST.get("ai_answer", None),
                "detect_language": request.POST.get("detect_language", None),
                "vector_ratio": vector_ratio,
                "top_k": top_k,
                "model": model,
                "context_tokens": context_tokens,
                "additional_instructions": urllib.parse.unquote_plus(
                    additional_instructions
                ),
                "trim_redundant": trim_redundant,
            }

            # Add advanced search parameters if applicable
            if advanced_mode:
                search_parameters.update(
                    {
                        "search_laws_option": request.POST.get(
                            "search_laws_option", "all"
                        ),
                        "language": request.POST.get("language", "all"),
                        "date_filter_option": request.POST.get(
                            "date_filter_option", "all"
                        ),
                    }
                )

                # Add specific law/enabling act selections
                if request.POST.get("search_laws_option") == "specific_laws":
                    search_parameters["laws"] = request.POST.getlist("laws")
                elif request.POST.get("search_laws_option") == "enabling_acts":
                    search_parameters["enabling_acts"] = request.POST.getlist(
                        "enabling_acts"
                    )

                # Add date filter parameters
                if request.POST.get("date_filter_option", "all") != "all":
                    search_parameters.update(
                        {
                            "in_force_date_start": request.POST.get(
                                "in_force_date_start"
                            ),
                            "in_force_date_end": request.POST.get("in_force_date_end"),
                            "last_amended_date_start": request.POST.get(
                                "last_amended_date_start"
                            ),
                            "last_amended_date_end": request.POST.get(
                                "last_amended_date_end"
                            ),
                        }
                    )

            # Create the LawSearch object
            from django.apps import apps

            LawSearch = apps.get_model("laws", "LawSearch")
            law_search = LawSearch.objects.create(
                user=request.user, query=query, search_parameters=search_parameters
            )

        # Cache sources so they can be retrieved in the AI answer function (keep for compatibility)
        query_uuid = uuid.uuid4()
        query_info = {
            "sources": sources,
            "query": query,
            "trim_redundant": trim_redundant,
            "model": model,
            "context_tokens": context_tokens,
            "additional_instructions": additional_instructions,
            "law_search_id": law_search.id if law_search else None,
        }
        cache.set(query_uuid, query_info, timeout=300)

        if law_search:
            law_search.query_uuid = str(query_uuid)
            law_search.save(update_fields=["query_uuid"])

        context = {
            "sources": sources_to_html(sources),
            "query": query,
            "query_uuid": query_uuid,
            "disable_llm": disable_llm,
            "law_search": law_search,
            "answer": law_search.ai_answer if law_search else None,
            # sparkline heights, suggestion flag, and original top_k
            "sparkline_heights": sparkline_heights,
            "show_increase_results": suggest_increase_results,
            "requested_top_k": top_k,
        }
    except Exception as e:
        context = {
            "sources": [],
            "query": query if "query" in locals() else None,
            "query_uuid": query_uuid,
            "disable_llm": True,
            "error": e,
        }
        logger.exception(
            f"Error in laws search for query: {context['query']}",
            query_uuid=query_uuid,
            error=e,
        )
        return render(request, "laws/search_result.html", context=context)

    response = render(
        request,
        "laws/search_result.html",
        context=context,
    )

    if not getattr(request, "_from_history", False):
        new_url = reverse("laws:view_search", args=[context["law_search"].id])
        # Remove trailing slash so the last path segment isn't empty when split
        response["HX-Push-Url"] = new_url.rstrip("/")

    return response


@app_access_required(app_name)
@login_required
def download_results(request, search_id):
    """Return a text file attachment containing the query and raw markdown of results."""
    logger.info("download_results called for search_id=%s", search_id)
    from .search_history.views import view_search

    law_search = get_object_or_404(LawSearch, id=search_id, user=request.user)
    query_uuid = law_search.query_uuid
    logger.info("Initial query_uuid from LawSearch: %s", query_uuid)
    found_cached_sources = True
    if not query_uuid:
        found_cached_sources = False
    query_info = cache.get(query_uuid)
    logger.info("cache.get(%s) returned: %r", query_uuid, query_info)
    if not query_info or "sources" not in query_info:
        found_cached_sources = False
    if not found_cached_sources:
        logger.info("Cache miss, rerunning search to repopulate cache")
        # Programmatically re-run the saved search to repopulate cache
        from django.http import QueryDict

        from .views import search as laws_search

        # Build POST data from stored form args
        post_data = QueryDict(mutable=True)
        for key, value in law_search.get_form_data().items():
            if isinstance(value, list):
                for v in value:
                    post_data.appendlist(key, v)
            else:
                post_data[key] = value

        # Simulate a POST request for replay
        original_method = request.method
        original_POST = request.POST
        request.method = "POST"
        request.POST = post_data
        request._from_history = True
        # Call the main search view
        laws_search(request, law_search)
        # Restore original request
        request.method = original_method
        request.POST = original_POST
        # Refresh to get new uuid
        law_search.refresh_from_db()
        query_uuid = law_search.query_uuid
        logger.info("After replay search, new query_uuid: %s", query_uuid)
        if not query_uuid:
            return HttpResponse("No stored results for this search.", status=400)
        query_info = cache.get(query_uuid)
        logger.info("cache.get(%s) after replay returned: %r", query_uuid, query_info)
        if not query_info or "sources" not in query_info:
            return HttpResponse(
                "Results not available. Please re-run the search.", status=404
            )
    query = query_info.get("query", "")
    sources = query_info.get("sources", [])

    # Build plain-text content
    lines = []
    lines.append(_("Query:"))
    lines.append(query)
    lines.append("")
    lines.append(_("Results:"))
    for s in sources:
        title = s.node.metadata.get("display_metadata", "")
        lines.append(f"# {title}\n")
        markdown_text = getattr(s.node, "text", "")
        lines.append(markdown_text)
        lines.append("\n---\n")  # Separator between results
    content = "\n".join(lines)

    response = HttpResponse(content, content_type="text/plain")
    law_search_translation = _("law_search")
    response["Content-Disposition"] = (
        f'attachment; filename="{law_search_translation}_{search_id}.txt"'
    )
    return response


def sources_to_html(sources):

    return [
        {
            "node_id": (
                urllib.parse.quote_plus(s.node.node_id)
                if "_schedule_" in s.node.node_id
                else urllib.parse.quote_plus(s.node.node_id).replace("+", "-")
            ),
            "title": get_display_title(s.node.metadata),
            "chunk": (
                s.node.metadata["chunk"]
                if not s.node.metadata["chunk"].endswith("/1")
                else None
            ),
            "headings": s.node.metadata.get("headings", None),
            "html": html_render(s.node.text, md),
        }
        for s in sources
    ]
