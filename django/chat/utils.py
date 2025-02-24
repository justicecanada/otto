import asyncio
import html
import json
import re
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
from llama_index.core.llms import ChatMessage
from llama_index.core.prompts import PromptType
from newspaper import Article
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from chat.forms import ChatOptionsForm
from chat.llm import OttoLLM
from chat.models import AnswerSource, Chat, Message
from chat.prompts import QA_PRUNING_INSTRUCTIONS
from otto.models import SecurityLabel
from otto.utils.common import display_cad_cost

logger = get_logger(__name__)
# Markdown instance
md = markdown.Markdown(
    extensions=["fenced_code", "nl2br", "tables", "extra"], tab_length=2
)


def copy_options(source_options, target_options, user=None, chat=None, mode=None):
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
    user = user or (request and request.user)
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
    if chat:
        target_options.chat = chat
    if mode:
        target_options.mode = mode
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

        yield sse_string(
            full_message, wrap_markdown=False, dots=False, remove_stop=True
        )
        # yield sse_string(full_message, wrap_markdown, dots=False, remove_stop=True)
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
            message.text = wrap_llm_response(full_message)  # full_message)
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


def generate_prompt(task_or_prompt: str):
    bind_contextvars(feature="prompt_generator")
    llm = OttoLLM()

    META_PROMPT = """
    Given a current prompt and a change description, produce a detailed system prompt to guide a language model in completing the task effectively.

    Your final output will be the full corrected prompt verbatim. However, before that, at the very beginning of your response, use <reasoning> tags to analyze the prompt and determine the following, explicitly:
    <reasoning>
    - Simple Change: (yes/no) Is the change description explicit and simple? (If so, skip the rest of these questions.)
    - Reasoning: (yes/no) Does the current prompt use reasoning, analysis, or chain of thought?
        - Identify: (max 10 words) if so, which section(s) utilize reasoning?
        - Conclusion: (yes/no) is the chain of thought used to determine a conclusion?
        - Ordering: (before/after) is the chain of though located before or after
    - Structure: (yes/no) does the input prompt have a well defined structure
    - Examples: (yes/no) does the input prompt have few-shot examples
        - Representative: (1-5) if present, how representative are the examples?
    - Complexity: (1-5) how complex is the input prompt?
        - Task: (1-5) how complex is the implied task?
        - Necessity: ()
    - Specificity: (1-5) how detailed and specific is the prompt? (not to be confused with length)
    - Prioritization: (list) what 1-3 categories are the MOST important to address.
    - Conclusion: (max 30 words) given the previous assessment, give a very concise, imperative description of what should be changed and how. this does not have to adhere strictly to only the categories listed
    </reasoning>

    # Guidelines

    - Understand the Task: Grasp the main objective, goals, requirements, constraints, and expected output.
    - Minimal Changes: If an existing prompt is provided, improve it only if it's simple. For complex prompts, enhance clarity and add missing elements without altering the original structure.
    - Reasoning Before Conclusions**: Encourage reasoning steps before any conclusions are reached. ATTENTION! If the user provides examples where the reasoning happens afterward, REVERSE the order! NEVER START EXAMPLES WITH CONCLUSIONS!
        - Reasoning Order: Call out reasoning portions of the prompt and conclusion parts (specific fields by name). For each, determine the ORDER in which this is done, and whether it needs to be reversed.
        - Conclusion, classifications, or results should ALWAYS appear last.
    - Examples: Include high-quality examples if helpful, using placeholders [in brackets] for complex elements.
    - What kinds of examples may need to be included, how many, and whether they are complex enough to benefit from placeholders.
    - Clarity and Conciseness: Use clear, specific language. Avoid unnecessary instructions or bland statements.
    - Formatting: Use markdown features for readability. DO NOT USE ``` CODE BLOCKS UNLESS SPECIFICALLY REQUESTED.
    - Preserve User Content: If the input task or prompt includes extensive guidelines or examples, preserve them entirely, or as closely as possible. If they are vague, consider breaking down into sub-steps. Keep any details, guidelines, examples, variables, or placeholders provided by the user.
    - Constants: DO include constants in the prompt, as they are not susceptible to prompt injection. Such as guides, rubrics, and examples.
    - Output Format: Explicitly the most appropriate output format, in detail. This should include length and syntax (e.g. short sentence, paragraph, JSON, etc.)
        - For tasks outputting well-defined or structured data (classification, JSON, etc.) bias toward outputting a JSON.
        - JSON should never be wrapped in code blocks (```) unless explicitly requested.

    The final prompt you output should adhere to the following structure below. Do not include any additional commentary, only output the completed system prompt. SPECIFICALLY, do not include any additional messages at the start or end of the prompt. (e.g. no "---")

    [Concise instruction describing the task - this should be the first line in the prompt, no section header]

    [Additional details as needed.]

    [Optional sections with headings or bullet points for detailed steps.]

    # Steps [optional]

    [optional: a detailed breakdown of the steps necessary to accomplish the task]

    # Output Format

    [Specifically call out how the output should be formatted, be it response length, structure e.g. JSON, markdown, etc]

    # Examples [optional]

    [Optional: 1-3 well-defined examples with placeholders if necessary. Clearly mark where examples start and end, and what the input and output are. User placeholders as necessary.]
    [If the examples are shorter than what a realistic example is expected to be, make a reference with () explaining how real examples should be longer / shorter / different. AND USE PLACEHOLDERS! ]

    # Notes [optional]

    [optional: edge cases, details, and an area to call or repeat out specific important considerations]
    [NOTE: you must start with a <reasoning> section. the immediate next token you produce should be <reasoning>]
    """.strip()

    completion = llm.chat_complete(
        [
            ChatMessage(role="system", content=META_PROMPT),
            ChatMessage(
                role="user", content="Task, Goal, or Current Prompt:\n" + task_or_prompt
            ),
        ]
    )

    usd_cost = llm.create_costs()
    cost = display_cad_cost(usd_cost)
    generated_prompt = re.sub(
        r"<reasoning>.*?</reasoning>", "", completion, flags=re.DOTALL
    ).strip()

    return generated_prompt, cost


<<<<<<< HEAD
def mark_sentences(text: str, good_matches: list) -> str:
    """
    TODO: Implement this function correctly

    Ignoring "\n" and "\r" characters in the text, wrap matching sentences in the text with <mark> tags.
    Return the original text with the sentences wrapped in <mark> tags, with original newlines preserved.
    """
    # Step 1: Replace newline characters with temporary markers.
    newline_marker = "<<<NEWLINE>>>"
    r_tag_marker = "<<<r_tag>>>"
    text_temp = text.replace("\n", newline_marker).replace("\r", r_tag_marker)

    # Step 2: For each sentence that should be marked, search and wrap it.
    for sentence in good_matches:
        # Remove leading/trailing whitespace and escape regex-special characters.
        sentence_clean = sentence.strip()
        # Escape regex special characters.
        escaped = re.escape(sentence_clean)
        # Replace literal spaces (escaped as "\ ") with a pattern that allows matching spaces or newline markers.
        # flexible_pattern = escaped.replace(r"\ ", r"(?:\s|<<<NEWLINE>>>)+")
        flexible_pattern = escaped.replace(
            r"\ ",
            r"(?:\s|"
            + re.escape(newline_marker)
            + r"|"
            + re.escape(r_tag_marker)
            + r")+",
        )
        pattern = re.compile(flexible_pattern, flags=re.IGNORECASE)
        # Wrap any match with <mark> tags.
        text_temp = pattern.sub(r"<mark>\g<0></mark>", text_temp)

    # Step 3: Restore original newlines.
    marked_text = text_temp.replace(newline_marker, "\n")
    return marked_text


def highlight_claims(claims_list, text, threshold=80):
    # match if the claims_list exist is text; if it does, then highlight it with <mark> tag
    from langdetect import detect
    from llama_index.core.schema import TextNode
    from sentence_splitter import split_text_into_sentences

    lang = detect(text)

    sentences = split_text_into_sentences(
        text=text.replace("\n", " ").replace("\r", " "),
        language="fr" if lang == "fr" else "en",
    )
    llm = OttoLLM()

    index = llm.temp_index_from_nodes(
        [TextNode(text=sentence) for sentence in sentences]
    )
    threshold = 0.7

    print("SENTENCES:")
    for sentence in sentences:
        print(sentence)

    print("CLAIMS:")
    for claim in claims_list:
        print(claim)

    good_matches = []
    for claim in claims_list:
        retriever = index.as_retriever()
        nodes = retriever.retrieve(claim)
        print("CLAIM:", claim)
        print("matches:")
        print([(node.score, node.node.text) for node in nodes])
        print("\n")
        for node in nodes:
            if node.score > threshold:
                good_matches.append(node.text)

    # TODO: Implement this function correctly
    text = mark_sentences(text, good_matches)

    # text = "\n".join(
    #     [
    #         f"<mark>{sentence}</mark>" if sentence in good_matches else sentence
    #         for sentence in sentences
    #     ]
    # )

    return text


def extract_claims_from_llm(llm_response_text):
    llm = OttoLLM()
    prompt = f"""
    Based on the following LLM response, extract key factual claims, including direct quotes.

    Respond in the format:
    <claim>whatever the claim is...</claim>
    <claim>another claim...</claim>

    etc.

    ---
    <llm_response>
    {llm_response_text}
    </llm_response>
    """
    claims_response = llm.complete(prompt)
    llm.create_costs()
    # find the claim tags and add whats wrapped in the claim tags to a list
    claims_list = re.findall(r"<claim>(.*?)</claim>", claims_response)
    return claims_list
=======
def fix_source_links(text, source_document_url):
    """
    Fix internal links in the text by merging them with the source document URL
    """

    def is_external_link(link):
        """
        Check if the link starts with "http"
        """
        return link.startswith("http")

    def is_anchor(link):
        """
        Check if the link starts with a "#"
        """
        return link.startswith("#")

    def merge_link_with_source(link, source_document_url):
        """
        Merge the link with the source document URL based on conditions
        """
        if link.startswith("/"):
            first_subdirectory = link.split("/")[1]
            # If the first subdirectory of the internal link is in the source url, it is merged at that point
            if "/" + first_subdirectory in source_document_url:
                source_document_url = source_document_url.split(
                    "/" + first_subdirectory
                )[0]
            # makes sure that we don't have double slashes in the URL
            elif source_document_url.endswith("/"):
                source_document_url = source_document_url[:-1]
        # makes sure we don't have a slash missing in the URL
        elif not source_document_url.endswith("/") and not is_anchor(link):
            source_document_url += "/"

        return source_document_url + link

    def remove_link(text, link_tuple):
        """
        Replace the link with plain text of the link text: "[text](url)" -> "text"
        """
        return text.replace(f"[{link_tuple[0]}]({link_tuple[1]})", f"{link_tuple[0]}")

    # Capture both the url and the text in a tuple, i.e., ('[text]', 'url')
    links = re.findall(r"\[(.*?)\]\((.*?)\)", text)

    # Check if there are internal links and merge them with the source URL
    for link_tuple in links:
        try:
            # The URL itself is the second group
            link = link_tuple[1]
            if not is_external_link(link):
                if source_document_url:
                    # Sometimes the internal link is followed by a space and some text like the name of the page
                    # e.g. (/wiki/Grapheme "Grapheme")
                    link = link.split(" ")[0]
                    # Merge the link with the source document URL
                    modified_link = merge_link_with_source(link, source_document_url)
                    text = text.replace(link, modified_link)
                else:
                    # makes sure we don't have unusable links in the text
                    text = remove_link(text, link_tuple)
        except:
            continue
    return wrap_llm_response(text)
>>>>>>> origin/main
