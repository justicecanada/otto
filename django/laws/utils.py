import asyncio

from django.core.cache import cache
from django.db import connections

import tiktoken
from asgiref.sync import sync_to_async

from chat.utils import llm_response_to_html


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
                "metadata": row[1],
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
    return _get_source_node(other_lang_node_id)


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


async def htmx_sse_response(response_gen, query, llm):
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
                yield formatted_response

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
                yield formatted_response
    except Exception as e:
        error = str(e)
        full_message = _("An error occurred:") + f"\n```\n{error}\n```"
        message_html = llm_response_to_html(full_message)
        message_html_lines = message_html.split("\n")

    await sync_to_async(llm.create_costs)()
    await sync_to_async(cache.delete)(f"sources_{query}")

    yield (
        f"data: <div hx-swap-oob='true' id='answer-sse'>"
        f"<div>{sse_joiner.join(message_html_lines)}</div></div>\n\n"
    )
