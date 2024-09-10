import asyncio
import urllib.parse

from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

import markdown
import tiktoken
from llama_index.core import ChatPromptTemplate
from llama_index.core.llms import ChatMessage, MessageRole
from structlog import get_logger

from chat.utils import llm_response_to_html
from otto.utils.decorators import app_access_required

from .models import Law
from .prompts import qa_prompt_instruction_tmpl, system_prompt

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


def _num_tokens(string: str, model_name: str) -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.encoding_for_model(model_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens


async def sync_generator_to_async(generator):
    for value in generator:
        yield value
        await asyncio.sleep(0)  # This will allow other async tasks to run


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
        {"id": "gpt-4o", "name": _("GPT-4o (Global)")},
        {"id": "gpt-4", "name": _("GPT-4 (Canada)")},
        {"id": "gpt-35", "name": _("GPT-3.5 (Canada)")},
    ]
    # if settings.GROQ_API_KEY:
    #     context["model_options"] += [
    #         {"id": "llama3-70b-8192", "name": "Llama3 70B (Groq)"},
    #         {"id": "llama3-8b-8192", "name": "Llama3 8B (Groq)"},
    #     ]

    return render(request, f"laws/advanced_search_form.html", context=context)


@app_access_required(app_name)
def answer(request):
    from llama_index.core import Settings
    from llama_index.core.response_synthesizers import CompactAndRefine
    from llama_index.core.schema import MetadataMode
    from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
    from llama_index.llms.azure_openai import AzureOpenAI

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

    # if "llama" in model:
    #     llm = Groq(model=model, api_key=settings.GROQ_API_KEY, temperature=0.1)
    # else:
    model_name = {
        "gpt-4o": "gpt-4o",
        "gpt-4": "gpt-4-turbo-preview",
        "gpt-35": "gpt-35-turbo",
    }[model]
    llm = AzureOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        azure_deployment=model,
        model=model_name,
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
            llm=llm,
            streaming=True,
            text_qa_template=CHAT_TEXT_QA_PROMPT,
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

    def process_bold_blocks(text, is_in_bold_block, bold_block):
        trimmed_text = text.strip()
        if trimmed_text.startswith("**"):
            if is_in_bold_block:
                # End of bold block
                bold_block += text
                is_in_bold_block = False
            else:
                # Start of a new bold block
                is_in_bold_block = True
                bold_block = text
        else:
            if is_in_bold_block:
                bold_block += text
            else:
                bold_block = None

        return is_in_bold_block, bold_block

    def format_html_response(full_message, sse_joiner):
        # Prevent code-format output
        # NOTE: the first replace is necessary to remove the word "markdown" that
        # sometimes appears after triple backticks
        tmp_full_message = full_message.replace("```markdown", "").replace("`", "")

        # Parse Markdown of full_message to HTML
        message_html = llm_response_to_html(tmp_full_message)
        message_html_lines = message_html.split("\n")
        if len(full_message) > 1:
            formatted_response = (
                f"data: <div>{sse_joiner.join(message_html_lines)}</div>\n\n"
            )

        else:
            formatted_response = None

        return (message_html_lines, formatted_response)

    def htmx_sse_response(response_gen):
        # time.sleep(60)
        sse_joiner = "\ndata: "
        full_message = ""
        message_html_lines = []
        try:
            is_in_bold_block = False
            bold_block = ""

            for text in response_gen:
                is_in_bold_block, bold_block_output = process_bold_blocks(
                    text, is_in_bold_block, bold_block
                )

                if bold_block_output is not None:
                    if is_in_bold_block:
                        bold_block = bold_block_output
                    else:
                        full_message += bold_block_output
                        bold_block = ""

                elif not is_in_bold_block:
                    full_message += text

                message_html_lines, formatted_response = format_html_response(
                    full_message, sse_joiner
                )
                if formatted_response is not None:
                    yield (formatted_response)

            # After the loop, handle any remaining bold block
            if is_in_bold_block and bold_block:
                if not bold_block.strip().endswith("**"):
                    bold_block += "**"
                if bold_block.strip() == "**":
                    bold_block = ""
                full_message += bold_block
                message_html_lines, formatted_response = format_html_response(
                    full_message, sse_joiner
                )
                if formatted_response is not None:
                    yield (formatted_response)
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
                azure_deployment=settings.DEFAULT_CHAT_MODEL,
                model=settings.DEFAULT_CHAT_MODEL,
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
