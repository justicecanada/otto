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


async def sync_generator_to_async(generator):
    for value in generator:
        yield value
        await asyncio.sleep(0)  # This will allow other async tasks to run


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


async def htmx_stream(
    chat,
    message_id,
    response_generator=None,  # Generator that yields partial responses (to be joined)
    response_replacer=None,  # Generator that yields complete responses (to replace previous response)
    response_str="",
    format=True,
    save_message=True,
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
    the response is finished. Set save_message=False to disable this behavior (for
    )
    """
    sse_joiner = "\ndata: "
    untitled_chat = chat.title.strip() == ""
    full_message = ""
    message_html_lines = []
    if dots:
        dots = f'<div class="typing" id="{message_id}-dots"> <span></span><span></span><span></span></div>'
    try:
        if response_generator:
            response_replacer = stream_to_replacer(response_generator)
        if response_str:
            response_replacer = stream_to_replacer([response_str])
        if response_replacer:
            async for response in response_replacer:
                if cache.get(f"stop_response_{message_id}", False):
                    if message_html_lines and message_html_lines[-1] == dots:
                        message_html_lines.pop()
                    break
                full_message = response
                if format:
                    message_html = llm_response_to_html(full_message)
                else:
                    message_html = full_message
                message_html_lines = message_html.split("\n")
                if dots:
                    message_html_lines.append(dots)
                yield f"data: <div>{sse_joiner.join(message_html_lines)}</div>\n\n"
                await asyncio.sleep(0.01)
            # Remove the dots once response is complete regardless of whether it was stopped
            if message_html_lines and message_html_lines[-1] == dots:
                message_html_lines.pop()
        else:
            # Static response (e.g. for summarize mode or any other non-streaming response)
            full_message = response_str
            if format:
                message_html = llm_response_to_html(full_message)
            else:
                message_html = full_message
            message_html_lines = message_html.split("\n")
    except Exception as e:
        error = str(e)
        import traceback

        print(f"ERROR: {str(e)}")
        print(traceback.format_exc())
        full_message = _("An error occurred:") + f"\n```\n{error}\n```"
        message_html = llm_response_to_html(full_message)
        message_html_lines = message_html.split("\n")

    yield (
        f"data: <div>{sse_joiner.join(message_html_lines)}</div>"
        "<div hx-swap-oob='true' id='stop-button'></div>\n\n"
    )
    # Now the response is finished, so save the response message
    if save_message:
        # Get the mode of the message from message_id
        mode = (await sync_to_async(Message.objects.get)(id=message_id)).mode
        message, is_created = await sync_to_async(Message.objects.update_or_create)(
            id=message_id,
            defaults={"chat": chat, "text": full_message, "is_bot": True, "mode": mode},
        )
    # Save the sources
    source_html_lines = ""
    # Update the chat security label in the sidebar if necessary
    new_chat_label_lines = ""
    if source_nodes and save_message:
        from librarian.models import Document

        sources = []
        for node in source_nodes:
            try:
                if node.node.text == "":
                    continue
                document = await sync_to_async(Document.objects.get)(
                    uuid_hex=node.node.ref_doc_id
                )
                score = node.score
                source = AnswerSource(
                    message=message,
                    document_id=document.id,
                    node_text=node.node.text,
                    node_score=score,
                    saved_citation=await sync_to_async(lambda: document.citation)(),
                )
                sources.append(source)
            except:
                print("Error saving source:", node)
        await sync_to_async(AnswerSource.objects.bulk_create)(sources)
        # Find the maximum security label of the sources
        security_labels = await sync_to_async(
            lambda: [
                source.document.data_source.security_label.acronym for source in sources
            ]
            + [chat.security_label.acronym]
        )()
        message.chat.security_label = await sync_to_async(SecurityLabel.maximum_of)(
            security_labels
        )
        await sync_to_async(message.chat.save)()
        new_chat_label = await sync_to_async(render_to_string)(
            "chat/components/chat_security_label.html",
            {
                "chat": message.chat,
                "security_labels": SecurityLabel.objects.all(),
                "swap_oob": True,
            },
        )
        new_chat_label_lines = new_chat_label.split("\n")
        # Add the sources (render message_sources.html)
        multiple_data_sources = len(set([s.document.data_source for s in sources])) > 1
        source_html = await sync_to_async(render_to_string)(
            "chat/components/message_sources.html",
            {
                "sources": sources,
                "message": message,
                "warn": multiple_data_sources and mode == "qa",
            },
        )
        source_html_lines = source_html.split("\n")
    yield (
        f"data: <div>{sse_joiner.join(message_html_lines)}</div>"
        f"<div class='sources row'>{sse_joiner.join(source_html_lines)}</div>\n\n"
    )
    # Generate a title for the chat if necessary
    final_response_str = "data: "
    if untitled_chat:
        chat_title = await sync_to_async(title_chat)(
            chat.id, force_title=False, llm=llm
        )
        if chat_title != "":
            final_response_str += (
                f"<span hx-swap-oob='true' id='current-chat-title'>"
                f"{chat_title}</span>"
            )

    # Create cost objects based on llm.input_tokens and llm.output_tokens
    if llm and save_message:
        user = await sync_to_async(lambda: chat.user)()
        costs = await sync_to_async(llm.create_costs)(user, message.mode)
        total_cost = sum([cost.usd_cost for cost in costs])
        print("COSTS:", costs)
        print("TOTAL COST:", total_cost)

    final_response_str += (
        f"<div hx-swap-oob='true' id='response-{message_id}'>"
        f"<div>{sse_joiner.join(message_html_lines)}</div>"
        f"<div class='sources row'>{sse_joiner.join(source_html_lines)}</div></div>"
        f"{sse_joiner.join(new_chat_label_lines)}"
        "\n\n"
    )
    yield final_response_str


def title_chat(chat_id, llm, force_title=True):
    # Assume costs will be calculated in the calling function where LLM instantiated
    chat = Chat.objects.get(id=chat_id)
    chat_messages = chat.messages.filter(pinned=False).order_by("date_created")
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
