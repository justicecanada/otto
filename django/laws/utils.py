import asyncio
import json
import uuid

from django.core.cache import cache
from django.db import connections
from django.utils.translation import gettext as _

import tiktoken
from asgiref.sync import sync_to_async
from structlog import get_logger

from chat.utils import wrap_llm_response
from otto.utils.common import display_cad_cost

logger = get_logger(__name__)


def num_tokens(string: str, model_name: str) -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.encoding_for_model(model_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens


def get_source_node(node_id):
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
                "metadata": json.loads(row[1]),
            }
    return None


def get_other_lang_node(node_id):
    # Replace "eng" with "fra" and vice versa
    lang = "eng" if "eng" in node_id else "fra"
    other_lang_node_id = (
        node_id.replace("eng", "fra")
        if lang == "eng"
        else node_id.replace("fra", "eng")
    )
    return get_source_node(other_lang_node_id)


def get_law_url(law, request_lang):
    ref = law.ref_number.replace(" ", "-").replace("/", "-")
    # Get user's language setting in Django
    lang = "fra" if request_lang == "fr" else "eng"
    # Constitution has special case
    if ref == "Const" and lang == "eng":
        return "https://laws-lois.justice.gc.ca/eng/Const/Const_index.html"
    if ref == "Const" and lang == "fra":
        return "https://laws-lois.justice.gc.ca/fra/ConstRpt/Const_index.html"
    if law.type == "act" and lang == "eng":
        return f"https://laws-lois.justice.gc.ca/eng/acts/{ref}/"
    if law.type == "act" and lang == "fra":
        return f"https://laws-lois.justice.gc.ca/fra/lois/{ref}/"
    if law.type == "regulation" and lang == "eng":
        return f"https://laws-lois.justice.gc.ca/eng/regulations/{ref}/"
    if law.type == "regulation" and lang == "fra":
        return f"https://laws-lois.justice.gc.ca/fra/reglements/{ref}/"


def format_llm_string(llm_string, wrap_sse=True):
    # Prevent code-format output
    # NOTE: the first replace is necessary to remove the word "markdown" that
    # sometimes appears after triple backticks
    llm_string = llm_string.replace("```markdown", "").replace("`", "")
    # Prevent asterisks from unfinished bolding from being rendered
    if llm_string.count("**") % 2 == 1:
        if llm_string.endswith("**"):
            llm_string = llm_string[:-2]
        else:
            llm_string += "**"
    # Fix broken links e.g. [Cannabis Regulations, Section 148] (#SOR-2018-144_eng_section_148)
    # (Remove space before the link)
    llm_string = llm_string.replace("] (", "](")
    if wrap_sse:
        return f"data: <div>{wrap_llm_response(llm_string)}</div>\n\n"
    return wrap_llm_response(llm_string)


async def htmx_sse_response(response_gen, llm, query_uuid):
    full_message = ""
    try:
        for text in response_gen:
            full_message += text
            if full_message:
                yield format_llm_string(full_message)
            await asyncio.sleep(0.01)
    except Exception as e:
        error_id = str(uuid.uuid4())[:7]
        full_message = format_llm_string(
            _("An error occurred while processing the request. ")
            + f" _({_('Error ID:')} {error_id})_"
        )
        logger.exception(
            f"Error in generating response",
            query_uuid=query_uuid,
            error_id=error_id,
            error=e,
        )

    cost = await sync_to_async(llm.create_costs)()
    display_cost = await sync_to_async(display_cad_cost)(cost)
    cost_div = f"<div class='mb-2 text-muted' style='font-size:0.875rem !important;'>{display_cost}</div>"
    markdown_div = format_llm_string(full_message, wrap_sse=False)
    if query_uuid:
        query_info = cache.get(query_uuid)
        query_info["answer"] = f"<div>{markdown_div}</div>{cost_div}"
        cache.set(query_uuid, query_info, timeout=300)

    yield (
        f"data: <div hx-swap-oob='true' id='answer-sse'>{markdown_div}</div><div id='answer-cost' hx-swap-oob='true'>{cost_div}</div>\n\n"
    )


async def htmx_sse_error(e="", query_uuid=None):
    error_message = _("An error occurred while processing the request. ")
    logger.error(
        f"Error in generating laws response: {e}",
        query_uuid=query_uuid,
    )

    yield (
        f"data: <div hx-swap-oob='true' id='answer-sse'>"
        f"<div>{error_message}</div></div>\n\n"
    )
