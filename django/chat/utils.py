import asyncio
import sys

from django.conf import settings
from django.core.cache import cache
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

import bleach
import markdown
import tiktoken
from asgiref.sync import sync_to_async
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
    yield response + "<<END>>"


async def htmx_stream(
    chat,
    message_id,
    response_generator=None,  # Generator that yields partial responses (to be joined) with <<END>>
    response_replacer=None,  # Generator that yields complete responses (to replace previous response) with <<END>>
    response_str="",
    format=True,
    save_message=True,
    dots=False,
    source_nodes=[],
    llm=None,
    user=None,
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

    If dots == True, typing dots will be added to the end of the response.
    When the <<END>> marker is received, the typing dots will no longer be shown.

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
        dots = '<div class="typing"> <span></span><span></span><span></span></div>'
    try:
        if response_generator:
            response_replacer = stream_to_replacer(response_generator)
        if response_str:
            response_str = stream_to_replacer([response_str])
        if response_replacer:
            async for response in response_replacer:
                if cache.get(f"stop_response_{message_id}", False):
                    if message_html_lines and message_html_lines[-1] == dots:
                        message_html_lines.pop()
                    break
                if response.endswith("<<END>>"):
                    full_message = response.replace("<<END>>", "")
                else:
                    full_message = response
                if format:
                    message_html = llm_response_to_html(full_message)
                else:
                    message_html = full_message
                message_html_lines = message_html.split("\n")
                if not response.endswith("<<END>>") and dots:
                    message_html_lines.append(dots)
                yield f"data: <div>{sse_joiner.join(message_html_lines)}</div>\n\n"
                await asyncio.sleep(0.01)
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
        costs = await sync_to_async(llm.create_costs)(user, "chat")
        total_cost = sum([cost.usd_cost for cost in costs])
        print("COSTS:", costs)
        print("TOTAL COST:", total_cost)
        final_response_str += f"<span style='color:red;'>Cost: ${total_cost:.2f}</span>"

    final_response_str += (
        f"<div hx-swap-oob='true' id='response-{message_id}'>"
        f"<div>{sse_joiner.join(message_html_lines)}</div>"
        f"<div class='sources row'>{sse_joiner.join(source_html_lines)}</div></div>"
        f"{sse_joiner.join(new_chat_label_lines)}"
        "\n\n"
    )
    yield final_response_str


def title_chat(chat_id, force_title=True, llm=None):
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


def tldr_summary(text):
    if len(text) == 0:
        return _("No text provided.")
    from llama_index.core.llms import ChatMessage
    from llama_index.llms.azure_openai import AzureOpenAI

    llm = AzureOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        deployment_name="gpt-35",
        model="gpt-35-turbo",  # TODO: Rethink how to pass this in. Maybe a global variable? Or dynamic based on the library?
        api_key=settings.AZURE_OPENAI_KEY,
        api_version=settings.AZURE_OPENAI_VERSION,
        temperature=0.3,
    )
    return llm.chat(
        [ChatMessage(role="user", content=text + "\n\nTL;DR:\n")]
    ).message.content


def summarize_long_text(
    text,
    length="short",
    target_language="en",
    custom_prompt=None,
    model=settings.DEFAULT_CHAT_MODEL,
):

    if len(text) == 0:
        return _("No text provided.")

    import asyncio

    from langchain.chains import MapReduceDocumentsChain, ReduceDocumentsChain
    from langchain.chains.combine_documents.stuff import StuffDocumentsChain
    from langchain.chains.llm import LLMChain
    from langchain.prompts import PromptTemplate
    from langchain.schema import Document
    from langchain.text_splitter import RecursiveCharacterTextSplitter
    from langchain_openai import AzureChatOpenAI

    # The "basic" prompts are the initial ones that jason wrote
    # the "orginal" ones are similar to the ones Michel wrote in the previous Summization tool but slightly reworded for better performance
    # Im keeping the prompts in a dictionary structure for now as we want to experiment with them and perhaps add more options in the future
    length_prompts = {
        # "basic": {
        #     "short": {
        #         "en": "Please summarize the main themes in a few sentences.",
        #         "fr": "Veuillez résumer les principaux thèmes en quelques phrases.",
        #     },
        #     "medium": {
        #         "en": "Please summarize the main themes in a few paragraphs.",
        #         "fr": "Veuillez résumer les principaux thèmes en quelques paragraphes.",
        #     },
        #     "long": {
        #         "en": "Please summarize the main themes in several verbose paragraphs.",
        #         "fr": "Veuillez résumer les principaux thèmes en plusieurs paragraphes verbeux.",
        #     },
        # },
        "original": {
            "short": {
                "en": "Rewrite the text (in English) in a short sized summary format. Make sure the summary is around two or three sentences and gives the reader a quick idea of what the text entails without going into too much details.",
                "fr": "Réécrivez le texte (en français) sous forme de résumé court. Assurez-vous que le résumé comporte environ deux ou trois phrases et donne au lecteur une idée rapide de ce que le texte contient sans entrer dans trop de détails.",
            },
            "medium": {
                "en": "Rewrite the text (in English) in a medium sized summary format and make sure the length is around two or three paragraphs.",
                "fr": "Réécrivez le texte (en français) dans un format de résumé de taille moyenne et assurez-vous que la longueur est de deux ou trois paragraphes.",
            },
            "long": {
                "en": (
                    "Rewrite the text (in English) as a detailed summary, using multiple paragraphs if necessary. (If the input is short, output 1 paragraph only)\n\n"
                    "Some rules to follow:\n"
                    '* Simply rewrite; do not say "This document is about..." etc. Include *all* important details.\n'
                    "* There is no length limit - be as detailed as possible. However, **do not extrapolate** on the text. The summary must be factual and not introduce any new ideas.\n"
                    "* The summary must not be longer than the input text.\n\n"
                    "Please rewrite the following document."
                ),
                "fr": (
                    "Réécrivez le texte (en anglais) sous forme de résumé détaillé, en utilisant plusieurs paragraphes si nécessaire. (Si la saisie est courte, affichez 1 seul paragraphe)\n\n"
                    "Quelques règles à suivre :\n"
                    '* Réécrivez simplement ; ne dites pas "Ce document concerne..." etc. Incluez *tous* les détails importants.\n'
                    "* Il n'y a pas de limite de longueur : soyez aussi détaillé que possible. Cependant, **n'extrapolez pas** sur le texte. Le résumé doit être factuel et ne pas introduire de nouvelles idées.\n"
                    "* Le résumé ne doit pas être plus long que le texte saisi.\n\n"
                    "Veuillez réécrire le document suivant."
                ),
            },
        },
        "tldr": {
            "short": {
                "en": "\n\nTL;DR (in English, in three or four sentences):\n",
                "fr": "\n\nTL;DR (en français, en trois ou quatre phrases):\n",
            },
            # "medium": {
            #     "en": "\n\n TL;DR in 300 words:\n",
            #     "fr": "\n\nTL;DR en 300 mots:\n",
            # },
            # "long": {
            #     "en": "\n\n TL;DR using 500 to 1000 words:\n",
            #     "fr": "\n\nTL;DR en utilisant 500 a 1000 mots:\n",
            # },
        },
    }

    total_tokens_text = num_tokens_from_string(text)
    print("TOTAL TOKENS", total_tokens_text)

    # we can customize which prompts we want in this section
    prompt_type = "tldr" if length == "short" else "original"

    length_prompt_en = length_prompts[prompt_type][length]["en"]
    length_prompt_fr = length_prompts[prompt_type][length]["fr"]
    if custom_prompt:
        length_prompt_en = custom_prompt
        length_prompt_fr = custom_prompt

    llm = AzureChatOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        model=model,
        azure_deployment=f"{model}",
        api_version=settings.AZURE_OPENAI_VERSION,
        api_key=settings.AZURE_OPENAI_KEY,
        temperature=0.2,
    )

    if total_tokens_text <= 16000:
        # if False:
        length_prompt = (
            length_prompt_fr if target_language == "fr" else length_prompt_en
        )
        length_prompt_template = (
            "{docs}" + length_prompt
            if prompt_type == "tldr"
            else length_prompt + "\n\n Document: {docs}"
        )
        if custom_prompt and "{docs}" in custom_prompt:
            length_prompt_template = custom_prompt
        elif custom_prompt:
            length_prompt_template = (
                custom_prompt
                + "\n\n The original document is below, enclosed in triple quotes:\n'''\n{docs}\n'''"
            )
        response = llm.invoke(length_prompt_template.format(docs=text)).content
    else:
        # prompts for beginning of map reduce
        map_template_en = (
            "The following are parts of a larger document:\n\n"
            "{docs}\n\n"
            "Based on these parts, rewrite the text in a summary that covers all important aspects of the document.\n"
            "Helpful Answer:"
        )
        map_template_fr = (
            "Les parties suivantes sont tirées d'un document plus long:\n\n"
            "{docs}\n\n"
            "À partir de ces parties, réécrivez le texte dans un résumé qui couvre tous les aspects importants du document."
            "Réponse utile:"
        )
        map_template = map_template_fr if target_language == "fr" else map_template_en
        map_prompt = PromptTemplate.from_template(map_template)

        # prompts for duration of recursion process
        reduce_template_en = (
            "The following are summaries generated based on a document:\n\n"
            "{docs}\n\n"
            f"Take these and rewrite it into a final summary. {length_prompt_en}. Return it in markdown format.\n"
            "Helpful Answer:"
        )
        reduce_template_fr = (
            "Les résumés suivants ont été générés à partir d'un document:\n\n"
            "{docs}\n\n"
            f"Prenez-les et réécrivez-le en un résumé final. {length_prompt_fr}. Renvoyez-le au format markdown.\n"
            "Réponse utile:"
        )
        reduce_template = (
            reduce_template_fr if target_language == "fr" else reduce_template_en
        )
        reduce_prompt = PromptTemplate.from_template(reduce_template)

        map_chain = LLMChain(llm=llm, prompt=map_prompt)

        # Run chain
        reduce_chain = LLMChain(llm=llm, prompt=reduce_prompt)

        # Takes a list of documents, combines them into a single string, and passes this to an LLMChain
        combine_documents_chain = StuffDocumentsChain(
            llm_chain=reduce_chain, document_variable_name="docs"
        )

        # Combines and iteratively reduces the mapped documents
        reduce_documents_chain = ReduceDocumentsChain(
            # This is final chain that is called.
            combine_documents_chain=combine_documents_chain,
            # If documents exceed context for `StuffDocumentsChain`
            collapse_documents_chain=combine_documents_chain,
            # The maximum number of tokens to group documents into.
            token_max=15000,
        )

        # Combining documents by mapping a chain over them, then combining results
        map_reduce_chain = MapReduceDocumentsChain(
            # Map chain
            llm_chain=map_chain,
            # Reduce chain
            reduce_documents_chain=reduce_documents_chain,
            # The variable name in the llm_chain to put the documents in
            document_variable_name="docs",
            # Return the results of the map steps in the output
            return_intermediate_steps=False,
        )

        # Split the text into chunks
        text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            chunk_size=12000, chunk_overlap=100
        )

        docs = [Document(page_content=text, metadata={"source": "userinput"})]

        split_docs = text_splitter.split_documents(docs)

        async def run_map_reduce():

            # Run the chain asynchronously and return the result when it's done
            return await map_reduce_chain.arun(split_docs)

        response = asyncio.run(run_map_reduce())
    return response


async def summarize_long_text_async(
    text, length="short", target_language="en", custom_prompt=None, model="gpt-4"
):
    return await sync_to_async(summarize_long_text)(
        text, length, target_language, custom_prompt, model
    )
