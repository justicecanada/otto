import asyncio
import html
import json
import sys
from itertools import groupby
from typing import AsyncGenerator, Generator

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.forms.models import model_to_dict
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

import markdown
import tiktoken
from asgiref.sync import sync_to_async
from data_fetcher.util import get_request
from llama_index.core import PromptTemplate
from llama_index.core.prompts import PromptType
from newspaper import Article
from structlog import get_logger

from chat.forms import ChatOptionsForm
from chat.llm import OttoLLM
from chat.models import AnswerSource, Chat, Message
from chat.prompts import QA_PRUNING_INSTRUCTIONS
from otto.models import SecurityLabel

logger = get_logger(__name__)
# Markdown instance
md = markdown.Markdown(
    extensions=["fenced_code", "nl2br", "tables", "extra"], tab_length=2
)


def copy_options(source_options, target_options, user=None):
    source_options = model_to_dict(source_options)
    # Remove the fields that are not part of the preset
    for field in ["id", "chat"]:
        source_options.pop(field)
    # Update the preset options with the dictionary
    fk_fields = ["qa_library"]
    m2m_fields = ["qa_data_sources", "qa_documents"]
    # Remove None values
    source_options = {k: v for k, v in source_options.items()}
    for key, value in source_options.items():
        if key in fk_fields:
            setattr(target_options, f"{key}_id", int(value) if value else None)
        elif key in m2m_fields:
            getattr(target_options, key).set(value)
        else:
            setattr(target_options, key, value)

    request = get_request()
    user = user or request.user
    if not target_options.qa_library or (
        user and not user.has_perm("librarian.view_library", target_options.qa_library)
    ):
        messages.warning(
            request,
            _(
                "QA library for settings preset not accessible. It has been reset to your personal library."
            ),
        )
        target_options.qa_library = user.personal_library
        target_options.qa_data_sources.clear()
        target_options.qa_documents.clear()
        target_options.qa_scope = "all"
        target_options.qa_mode = "rag"
    target_options.save()


def num_tokens_from_string(string: str, model: str = "gpt-4") -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.encoding_for_model(model)
    num_tokens = len(encoding.encode(string))
    return num_tokens


def wrap_llm_response(llm_response_str):
    return f'<div class="markdown-text" data-md="{html.escape(json.dumps(str(llm_response_str)))}"></div>'


def url_to_text(url):
    article = Article(url)
    article.config.MAX_TEXT = sys.maxsize
    try:
        article.download()
        article.parse()
        return article.text
    except:
        return ""


async def stream_to_replacer(response_stream, attribute=None):
    response = ""
    try:
        async for chunk in response_stream:
            response += getattr(chunk, attribute) if attribute else chunk
            yield response
    except:
        for chunk in response_stream:
            response += getattr(chunk, attribute) if attribute else chunk
            yield response
            await asyncio.sleep(0)  # This will allow other async tasks to run


def save_sources_and_update_security_label(source_nodes, message, chat):
    from librarian.models import Document

    sources = []
    for i, group in enumerate(source_nodes):
        for node in group:
            try:
                if node.node.text == "":
                    continue
                document = Document.objects.get(uuid_hex=node.node.ref_doc_id)
                score = node.score
                source = AnswerSource.objects.create(
                    message=message,
                    document_id=document.id,
                    node_text=node.node.text,
                    node_id=node.id_,
                    node_score=score,
                    group_number=i,
                )
                sources.append(source)
            except Exception as e:
                logger.debug("Error saving source:", node, e)

    security_labels = [
        source.document.data_source.security_label.acronym for source in sources
    ] + [chat.security_label.acronym]

    message.chat.security_label = SecurityLabel.maximum_of(security_labels)
    message.chat.save()


def close_md_code_blocks(text):
    # Close any open code blocks
    if text.count("```") % 2 == 1:
        text += "\n```"
    elif text.count("`") % 2 == 1:
        text += "`"
    return text


async def htmx_stream(
    chat: Chat,
    message_id: int,
    llm: OttoLLM,
    response_generator: Generator = None,
    response_replacer: AsyncGenerator = None,
    response_str: str = "",
    wrap_markdown: bool = True,
    dots: bool = False,
    source_nodes: list = [],
    switch_mode: bool = False,
    remove_stop: bool = False,
) -> AsyncGenerator:
    """
    Formats responses into HTTP Server-Sent Events (SSE) for HTMX streaming.
    This function is a generator that yields SSE strings (lines starting with "data: ").

    There are 3 ways to use this function:
    1. response_generator: A custom generator that yields response chunks.
       Each chunk will be *appended* to the previous chunk.
    2. response_replacer: A custom generator that yields complete response strings.
       Unlike response_generator, each response will *replace* the previous response.
    3. response_str: A static response string.

    If dots is True, typing dots will be added to the end of the response.

    The function typically expects markdown responses from LLM, but can also handle
    HTML responses from other sources. Set wrap_markdown=False for plain HTML output.

    By default, the response will be saved as a Message object in the database after
    the response is finished. Set save_message=False to disable this behavior.
    """

    # Helper function to format a string as an SSE message
    def sse_string(
        message: str, wrap_markdown=True, dots=False, remove_stop=False
    ) -> str:
        sse_joiner = "\ndata: "
        if wrap_markdown:
            message = wrap_llm_response(message)
        if dots:
            message += dots
        out_string = "data: "
        out_string += sse_joiner.join(message.split("\n"))
        if remove_stop:
            out_string += "<div hx-swap-oob='true' id='stop-button'></div>"
        out_string += "\n\n"  # End of SSE message
        return out_string

    ##############################
    # Start of the main function #
    ##############################
    is_untitled_chat = chat.title.strip() == ""
    full_message = ""
    stop_warning_message = _(
        "Response stopped early. Costs may still be incurred after stopping."
    )
    generation_stopped = False
    dots_html = '<div class="typing"><span></span><span></span><span></span></div>'
    if dots:
        dots = dots_html
    if switch_mode:
        mode = chat.options.mode
        mode_str = {"qa": _("Q&A"), "chat": _("Chat")}[mode]

    try:
        if response_generator:
            response_replacer = stream_to_replacer(response_generator)
        if response_str:
            response_replacer = stream_to_replacer([response_str])

        # Stream the response text
        first_message = True
        async for response in response_replacer:
            if first_message and switch_mode:
                full_message = render_to_string(
                    "chat/components/mode_switch_message.html",
                    {
                        "mode": mode,
                        "mode_str": mode_str,
                        "library_id": chat.options.qa_library_id,
                        "library_str": chat.options.qa_library.name,
                    },
                )
                yield sse_string(
                    full_message,
                    wrap_markdown=False,
                    dots=dots_html,
                    remove_stop=remove_stop,
                )
                await asyncio.sleep(1)
                first_message = False

            if response != "<|batchboundary|>":
                if remove_stop or not cache.get(f"stop_response_{message_id}", False):
                    full_message = response
                elif not generation_stopped:
                    generation_stopped = True
                    if wrap_markdown:
                        full_message = close_md_code_blocks(full_message)
                        stop_warning_message = f"\n\n_{stop_warning_message}_"
                    else:
                        stop_warning_message = f"<p><em>{stop_warning_message}</em></p>"
                    full_message = f"{full_message}{stop_warning_message}"
                    message = await sync_to_async(Message.objects.get)(id=message_id)
                    message.text = full_message
                    await sync_to_async(message.save)()
            elif generation_stopped:
                break
            # Avoid overwhelming client with markdown rendering:
            # slow down yields if the message is large
            length = len(full_message)
            yield_every = length // 2000 + 1
            if length < 1000 or length % yield_every == 0:
                yield sse_string(
                    full_message,
                    wrap_markdown,
                    dots=dots if not generation_stopped else False,
                    remove_stop=remove_stop or generation_stopped,
                )
            await asyncio.sleep(0.01)

        yield sse_string(full_message, wrap_markdown, dots=False, remove_stop=True)
        await asyncio.sleep(0.01)

        await sync_to_async(llm.create_costs)()

        message = await sync_to_async(Message.objects.get)(id=message_id)
        message.text = full_message
        await sync_to_async(message.save)()

        if is_untitled_chat:
            title_llm = OttoLLM()
            await sync_to_async(title_chat)(chat.id, force_title=False, llm=title_llm)
            await sync_to_async(title_llm.create_costs)()

        # Update message text with markdown wrapper to pass to template
        if wrap_markdown:
            message.text = wrap_llm_response(full_message)
        context = {"message": message, "swap_oob": True, "update_cost_bar": True}

        # Save sources and security label
        if source_nodes:
            await sync_to_async(save_sources_and_update_security_label)(
                source_nodes, message, chat
            )
            context["security_labels"] = await sync_to_async(
                SecurityLabel.objects.all
            )()

    except Exception as e:
        message = await sync_to_async(Message.objects.get)(id=message_id)
        full_message = _("An error occurred.")
        import traceback

        traceback.print_exc()
        message.text = full_message
        await sync_to_async(message.save)()
        message.text = wrap_llm_response(full_message)
        context = {"message": message, "swap_oob": True}

    # Render the message template, wrapped in SSE format
    context["message"].json = json.dumps(str(full_message))
    yield sse_string(
        await sync_to_async(render_to_string)(
            "chat/components/chat_message.html", context
        ),
        wrap_markdown=False,
        remove_stop=True,
    )


def title_chat(chat_id, llm, force_title=True):
    # Assume costs will be calculated in the calling function where LLM instantiated
    chat = Chat.objects.get(id=chat_id)
    chat_messages = chat.messages.order_by("date_created")
    if not force_title and (
        chat.title != ""
        or (
            len(chat_messages) < 3
            and len(" ".join([m.text for m in chat_messages])) < 300
        )
    ):
        return chat.title

    chat_messages_text = []
    for message in chat_messages[:7]:
        if message.text:
            chat_messages_text.append(message.text)
        elif message.files.exists():
            chat_messages_text.append(message.mode + " " + _("with files:"))
            for file in message.files.all():
                chat_messages_text.append(file.filename)
    chat_text = "\n".join([message[:500] for message in chat_messages_text])
    if len(chat_text) < 3:
        return _("Untitled chat")
    chat_text = chat_text[:2000]
    prompt = (
        "Write a concise title (1-4 words) to the following chat. "
        "Some examples are: 'DG meeting notes', 'Pancake recipe', 'Feedback session'.\n"
        "You must respond with at least one word:\n---\n"
        f"{chat_text}\n---\n"
        "TITLE: "
    )
    try:
        generated_title = llm.complete(prompt)[:254]
        if generated_title.startswith('"') and generated_title.endswith('"'):
            generated_title = generated_title[1:-1]
    except Exception as e:
        generated_title = _("Untitled chat")
    chat.title = generated_title
    chat.save()
    return generated_title


def summarize_long_text(
    text,
    llm,
    length="short",
    target_language="en",
    custom_prompt=None,
    gender_neutral=True,
    instructions=None,
):

    gender_neutral_instructions = {
        "en": "Avoid personal pronouns unless the person's gender is clearly indicated.",
        "fr": "Évitez les pronoms personnels sauf si le genre de la personne est clairement indiqué.",
    }

    if len(text) == 0:
        return _("No text provided.")

    length_prompts = {
        "short": {
            "en": """<document>
{docs}
</document>
<instruction>
Write a TL;DR summary of document in English - 3 or 4 sentences max. If document is shorter than this, just output the document verbatim.
</instruction>
TL;DR:
""",
            "fr": """<document>
{docs}
</document>
<instruction>
Écrivez un résumé "TL;DR" en français - 3 ou 4 phrases maximum. Si le document est plus court, affichez-le tel quel.
</instruction>
Résumé :
""",
        },
        "medium": {
            "en": """<document>
{docs}
</document>
<instruction>
Rewrite the text (in English) in a medium sized summary format and make sure the length is around two or three paragraphs. If document is shorter than this, just output the document verbatim.
</instruction>
Summary:
""",
            "fr": """<document>
{docs}
</document>
<instruction>
Réécrivez le texte (en anglais) sous forme de résumé moyen et assurez-vous que la longueur est d'environ deux ou trois paragraphes. Si le document est plus court, affichez-le tel quel.
</instruction>
Résumé :
""",
        },
        "long": {
            "en": """<document>
{docs}
</document>
<instruction>
Rewrite the text (in English) as a detailed summary, using multiple paragraphs if necessary. (If the input is short, output 1 paragraph only)

Some rules to follow:
* Simply rewrite; do not say "This document is about..." etc. Include *all* important details.
* There is no length limit - be as detailed as possible.
* **Never extrapolate** on the text. The summary must be factual and not introduce any new ideas.
* If document is short, just output the document verbatim.
</instruction>
Detailed summary:
""",
            "fr": """<document>
{docs}
</document>
<instruction>
Réécrivez le texte (en anglais) sous forme de résumé détaillé, en utilisant plusieurs paragraphes si nécessaire. (Si la saisie est courte, affichez 1 seul paragraphe)

Quelques règles à suivre :
* Réécrivez simplement ; ne dites pas "Ce document concerne..." etc. Incluez *tous* les détails importants.
* Il n'y a pas de limite de longueur : soyez aussi détaillé que possible.
* **Ne faites jamais d'extrapolation** sur le texte. Le résumé doit être factuel et ne doit pas introduire de nouvelles idées.
* Si le document est court, affichez-le tel quel.
</instruction>
Résumé détaillé :
""",
        },
    }

    if custom_prompt and "{docs}" in custom_prompt:
        length_prompt_template = custom_prompt
    elif custom_prompt:
        length_prompt_template = (
            """
<document>
{docs}
</document>
<instruction>
"""
            + f"{custom_prompt}\n</instruction>"
        )
    else:
        length_prompt_template = length_prompts[length][target_language]
        if gender_neutral:
            length_prompt_template = length_prompt_template.replace(
                "</instruction>",
                gender_neutral_instructions[target_language] + "\n</instruction>",
            )
        if instructions:
            length_prompt_template = length_prompt_template.replace(
                "</instruction>", instructions + "\n</instruction>"
            )

    # Tree summarizer prompt requires certain variables
    # Note that we aren't passing in a query here, so the query will be empty
    length_prompt_template = length_prompt_template.replace(
        "{docs}", "{context_str}{query_str}"
    )
    template = PromptTemplate(length_prompt_template, prompt_type=PromptType.SUMMARY)

    response = llm.tree_summarize(
        context=text,
        query="",
        template=template,
    )
    return response


async def summarize_long_text_async(
    text,
    llm,
    length="short",
    target_language="en",
    custom_prompt=None,
):
    return await sync_to_async(summarize_long_text)(
        text, llm, length, target_language, custom_prompt
    )


def get_source_titles(sources):
    return [
        source.metadata.get("title", source.metadata["source"]) for source in sources
    ]


def create_batches(iterable, n=1):
    length = len(iterable)
    for ndx in range(0, length, n):
        yield iterable[ndx : min(ndx + n, length)]


async def combine_response_generators(generators, titles, query, llm, prune=False):
    streams = [{"stream": stream, "status": "running"} for stream in generators]
    final_streams = [f"\n###### *{title}*\n" for title in titles]
    while any([stream["status"] == "running" for stream in streams]):
        for i, stream in enumerate(streams):
            try:
                if stream["status"] == "running":
                    final_streams[i] += next(stream["stream"])
            except StopIteration:
                stream["status"] = "stopped"
                if prune:
                    tmpl = PromptTemplate(QA_PRUNING_INSTRUCTIONS).format(
                        query_str=query, answer_str=final_streams[i]
                    )
                    relevance_check = llm.complete(tmpl)
                    if relevance_check is None:
                        relevance_check = "yes"
                    if str(relevance_check).lower().startswith("no"):
                        final_streams[i] = ""

        final_result = "\n\n---\n\n".join(
            [stream for stream in final_streams if stream]
        )
        if final_result:
            yield (final_result)
        else:
            yield (_("**No relevant sources found.**"))
        await asyncio.sleep(0)


async def combine_response_replacers(generators, titles):
    streams = [{"stream": stream, "status": "running"} for stream in generators]
    formatted_titles = [f"\n###### *{title}*\n" for title in titles]
    partial_streams = ["" for _ in titles]
    final_streams = ["" for _ in titles]
    while any([stream["status"] == "running" for stream in streams]):
        for i, stream in enumerate(streams):
            try:
                if stream["status"] == "running":
                    partial_streams[i] = await stream["stream"].__anext__()
                    final_streams[i] = formatted_titles[i] + partial_streams[i]
            except StopAsyncIteration:
                stream["status"] = "stopped"
            except Exception as e:
                final_streams[i] = (
                    formatted_titles[i] + f'_{_("Error generating response.")}_'
                )
                stream["status"] = "stopped"
        yield ("\n\n---\n\n".join(final_streams))
        await asyncio.sleep(0)


async def combine_batch_generators(generators, pruning=False):
    # Given a list of generators from either combine_response_replacers or
    # combine_response_generators, make one generator across batches for htmx_stream
    final_streams = []
    for generator in generators:
        stream = "\n\n---\n\n".join(final_streams)
        async for response in generator:
            if stream:
                # Add line between already-streamed batches and streaming batch
                stream_value = stream + "\n\n---\n\n" + response
            else:
                # Don't need line if there's nothing streamed
                stream_value = response
            yield stream_value
            await asyncio.sleep(0)
        if pruning and response == _("**No relevant sources found.**"):
            # If we're pruning (combine_response_generators only) and nothing
            # relevant was found in the batch, just retain previous batches
            yield stream
            await asyncio.sleep(0)
        else:
            final_streams.append(response)
        yield "<|batchboundary|>"

    if not final_streams and pruning:
        # If we're pruning (combine_response_generators only) and have nothing after
        # iterating through all batches, stream the pruning message again
        yield _("**No relevant sources found.**")


def group_sources_into_docs(source_nodes):
    doc_key = lambda x: x.node.ref_doc_id

    doc_group_iters = groupby(
        sorted(source_nodes, key=lambda x: (doc_key(x), x.metadata["chunk_number"])),
        key=doc_key,
    )

    # Nested list makes downstream manipulations (e.g. sorting by scores) easier
    doc_groups = [list(doc) for _, doc in doc_group_iters]

    return doc_groups


def sort_by_max_score(groups):
    # Sort groups of nodes by the maximum relevance score within each group
    # TODO: consider using average score within each group instead

    return sorted(
        groups,
        key=lambda doc: max(node.score for node in doc),
        reverse=True,
    )


def change_mode_to_chat_qa(chat):
    chat.options.mode = "qa"
    chat.options.qa_library = chat.user.personal_library
    chat.options.qa_scope = "data_sources"
    chat.options.qa_data_sources.set([chat.data_source])
    chat.options.save()

    return render_to_string(
        "chat/components/chat_options_accordion.html",
        {
            "options_form": ChatOptionsForm(instance=chat.options, user=chat.user),
            "mode": "qa",
            "swap": "true",
        },
    )


def bad_url(render_markdown=False):
    out = _("Sorry, that URL isn't allowed. Otto can only access sites ending in:")
    out += "\n\n"
    out += "\n".join([f"* `{url}`" for url in settings.ALLOWED_FETCH_URLS]) + "\n\n"
    out += (
        _("(e.g., `justice.gc.ca` or `www.tbs-sct.canada.ca` are also allowed)")
        + "\n\n"
    )
    out += _("As a workaround, you can save the content to a file and upload it here.")

    if render_markdown:
        out = md.convert(out)
    return out
