import asyncio
import sys

from django.core.cache import cache
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

import bleach
import markdown
import tiktoken
from asgiref.sync import sync_to_async
from llama_index.core import PromptTemplate
from llama_index.core.prompts import PromptType
from newspaper import Article

from chat.models import AnswerSource, Chat, Message
from otto.models import SecurityLabel

# Markdown instance
md = markdown.Markdown(
    extensions=["fenced_code", "nl2br", "tables", "extra"], tab_length=2
)


def num_tokens_from_string(string: str, model: str = "gpt-4") -> int:
    """Returns the number of tokens in a text string."""
    encoding = tiktoken.encoding_for_model(model)
    num_tokens = len(encoding.encode(string))
    return num_tokens


def llm_response_to_html(llm_response_str):
    s = str(llm_response_str)
    # When message has uneven # of '```' then add a closing '```' on a newline
    if s.count("```") % 2 == 1:
        s += "\n```"
    raw_html = md.convert(s)
    # return raw_html
    allowed_tags = [
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "b",
        "i",
        "strong",
        "em",
        "tt",
        "p",
        "br",
        "span",
        "div",
        "blockquote",
        "code",
        "pre",
        "hr",
        "ul",
        "ol",
        "li",
        "dd",
        "dt",
        "img",
        "a",
        "sub",
        "sup",
        "table",
        "thead",
        "th",
        "tbody",
        "tr",
        "td",
        "tfoot",
        "dl",
    ]
    allowed_attributes = {
        "*": ["id"],
        "img": ["src", "alt", "title"],
        "a": ["href", "alt", "title"],
        "pre": ["class"],
        "code": ["class"],
        "span": ["class"],
    }
    clean_html = bleach.clean(raw_html, allowed_tags, allowed_attributes)
    return clean_html


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
    for node in source_nodes:
        try:
            if node.node.text == "":
                continue
            document = Document.objects.get(uuid_hex=node.node.ref_doc_id)
            score = node.score
            source = AnswerSource(
                message=message,
                document_id=document.id,
                node_text=node.node.text,
                node_score=score,
                saved_citation=document.citation,
            )
            sources.append(source)
        except Exception as e:
            print("Error saving source:", node, e)

    AnswerSource.objects.bulk_create(sources)

    security_labels = [
        source.document.data_source.security_label.acronym for source in sources
    ] + [chat.security_label.acronym]

    message.chat.security_label = SecurityLabel.maximum_of(security_labels)
    message.chat.save()


async def htmx_stream(
    chat,
    message_id,
    response_generator=None,  # Generator that yields partial responses (to be joined)
    response_replacer=None,  # Generator that yields complete responses (to replace previous response)
    response_str="",
    format=True,
    dots=False,
    source_nodes=[],
    llm=None,
):
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
    HTML responses from other sources. Set format=False to disable markdown parsing.

    By default, the response will be saved as a Message object in the database after
    the response is finished. Set save_message=False to disable this behavior.
    """

    # Helper function to format a string as an SSE message
    def sse_string(message, format=True, dots=False, remove_stop=False):
        sse_joiner = "\ndata: "
        if format:
            message = llm_response_to_html(message)
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
    if dots:
        dots = f'<div class="typing"><span></span><span></span><span></span></div>'

    try:
        if response_generator:
            response_replacer = stream_to_replacer(response_generator)
        if response_str:
            response_replacer = stream_to_replacer([response_str])

        # Stream the response text
        async for response in response_replacer:
            full_message = response
            if cache.get(f"stop_response_{message_id}", False):
                break
            yield sse_string(full_message, format, dots)
            await asyncio.sleep(0.01)

        yield sse_string(full_message, format, dots=False, remove_stop=True)

        # Response is done! Update costs and save message
        message = await sync_to_async(Message.objects.get)(id=message_id)
        if llm:
            user = await sync_to_async(lambda: chat.user)()
            # TODO: Add costs for translation messages
            costs = await sync_to_async(llm.create_costs)(user, message.mode)
            print(costs)
            total_cost = sum([cost.usd_cost for cost in costs])
            message.cost = total_cost
        message.text = full_message
        await sync_to_async(message.save)()

        if is_untitled_chat:
            await sync_to_async(title_chat)(chat.id, force_title=False, llm=llm)

        # Update message text with HTML formatting to pass to template
        message.text = llm_response_to_html(full_message)
        context = {"message": message, "swap_oob": True}

        # Save sources and security label
        if source_nodes:
            await sync_to_async(save_sources_and_update_security_label)(
                source_nodes, message, chat
            )
            context["security_labels"] = await sync_to_async(
                SecurityLabel.objects.all
            )()

    except Exception as e:
        full_message = _("An error occurred:") + f"\n```\n{str(e)}\n```"
        message.text = full_message
        await sync_to_async(message.save)()
        message.text = llm_response_to_html(full_message)
        context = {"message": message, "swap_oob": True}

    # Render the message template, wrapped in SSE format
    yield sse_string(
        await sync_to_async(render_to_string)(
            "chat/components/chat_message.html", context
        ),
        format=False,
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
):

    if len(text) == 0:
        return _("No text provided.")

    length_prompts = {
        "short": {
            "en": "{docs}\n\nTL;DR (in English, in three or four sentences):\n",
            "fr": "{docs}\n\nTL;DR (en français, en trois ou quatre phrases):\n",
        },
        "medium": {
            "en": "Rewrite the text (in English) in a medium sized summary format and make sure the length is around two or three paragraphs.\n\n Document: {docs}",
            "fr": "Réécrivez le texte (en français) dans un format de résumé de taille moyenne et assurez-vous que la longueur est de deux ou trois paragraphes.\n\n Document: {docs}",
        },
        "long": {
            "en": (
                "Rewrite the text (in English) as a detailed summary, using multiple paragraphs if necessary. (If the input is short, output 1 paragraph only)\n\n"
                "Some rules to follow:\n"
                '* Simply rewrite; do not say "This document is about..." etc. Include *all* important details.\n'
                "* There is no length limit - be as detailed as possible. However, **do not extrapolate** on the text. The summary must be factual and not introduce any new ideas.\n"
                "* The summary must not be longer than the input text.\n\n"
                "Please rewrite the following document."
                "\n\n Document: {docs}"
            ),
            "fr": (
                "Réécrivez le texte (en anglais) sous forme de résumé détaillé, en utilisant plusieurs paragraphes si nécessaire. (Si la saisie est courte, affichez 1 seul paragraphe)\n\n"
                "Quelques règles à suivre :\n"
                '* Réécrivez simplement ; ne dites pas "Ce document concerne..." etc. Incluez *tous* les détails importants.\n'
                "* Il n'y a pas de limite de longueur : soyez aussi détaillé que possible. Cependant, **n'extrapolez pas** sur le texte. Le résumé doit être factuel et ne pas introduire de nouvelles idées.\n"
                "* Le résumé ne doit pas être plus long que le texte saisi.\n\n"
                "Veuillez réécrire le document suivant."
                "\n\n Document: {docs}"
            ),
        },
    }

    length_prompt_template = length_prompts[length][target_language]
    if custom_prompt and "{docs}" in custom_prompt:
        length_prompt_template = custom_prompt
    elif custom_prompt:
        length_prompt_template = (
            custom_prompt
            + "\n\n"
            + _("The original document is below, enclosed in triple quotes:")
            + "\n'''\n{docs}\n'''"
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
