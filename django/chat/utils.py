import asyncio
import html
import json
import re
import time
import uuid
from itertools import groupby
from typing import AsyncGenerator, Generator

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.forms.models import model_to_dict
from django.http import StreamingHttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

import markdown
import tiktoken
from asgiref.sync import sync_to_async
from data_fetcher import cache_within_request
from data_fetcher.util import get_request
from llama_index.core.base.llms.types import DocumentBlock, ImageBlock, TextBlock
from llama_index.core.llms import ChatMessage, MessageRole
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from otto.models import Cost, Notification
from otto.utils.common import cad_cost, display_cad_cost

from chat._utils.estimate_cost import estimate_cost_of_request  # Do not remove!
from chat.forms import ChatOptionsForm
from chat.llm import OttoLLM
from chat.models import AnswerSource, Chat, ChatOptions, Message
from chat.prompts import current_time_prompt

logger = get_logger(__name__)
# Markdown instance
md = markdown.Markdown(
    extensions=["fenced_code", "nl2br", "tables", "extra"], tab_length=2
)

SSE_KEEPALIVE_INTERVAL_SECONDS = 10
SSE_RENDER_THROTTLE_MESSAGE_LENGTH = 8000
SSE_RENDER_THROTTLE_INTERVAL_SECONDS = 0.12


def get_request_route_label(request):
    """Return a normalized low-cardinality label for a request route."""
    if not request:
        return None

    resolver_match = getattr(request, "resolver_match", None)
    for attr in ("route", "view_name", "url_name"):
        value = getattr(resolver_match, attr, None)
        if value:
            return value

    path = getattr(request, "path", "") or ""
    if not path:
        return None

    path = re.sub(r"/[0-9]+(?=/|$)", "/<id>", path)
    path = re.sub(r"/[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}(?=/|$)", "/<uuid>", path)
    return path


def classify_legacy_sse_stream(stream_summary):
    """Classify the dominant phase of a legacy SSE request for observability."""
    duration_ms = stream_summary.get("stream_duration_ms") or 0
    wait_ms = stream_summary.get("processing_wait_ms") or 0
    response_char_count = stream_summary.get("response_char_count") or 0

    if stream_summary.get("generation_stopped"):
        request_phase = "stopped_early"
    elif (
        wait_ms
        and duration_ms
        and wait_ms
        >= max(
            5000,  # at least 5 s of wait regardless of proportion
            int(duration_ms * 0.5),  # or >= 50 % of total duration
        )
    ):
        request_phase = "wait_heavy"
    elif wait_ms:
        request_phase = "wait_then_generate"
    elif response_char_count >= 50000:  # ~50 k chars ≈ a very large generated response
        request_phase = "large_generation"
    else:
        request_phase = "generation_heavy"

    return {
        "route": stream_summary.get("route"),
        "workload_kind": stream_summary.get("workload_kind"),
        "request_phase": request_phase,
        "stream_duration_ms": duration_ms,
        "processing_wait_ms": wait_ms,
        "response_char_count": response_char_count,
        "document_count": stream_summary.get("document_count"),
        "success_document_count": stream_summary.get("success_document_count"),
        "error_document_count": stream_summary.get("error_document_count"),
        "qa_process_mode": stream_summary.get("qa_process_mode"),
        "generation_stopped": stream_summary.get("generation_stopped"),
    }


def build_stream_context(request, workload_kind, **extra):
    """Build a structured logging context dict for an SSE stream."""
    stream_context = {
        "route": get_request_route_label(request),
        "workload_kind": workload_kind,
    }
    stream_context.update(extra)
    return {k: v for k, v in stream_context.items() if v is not None}


PLACEHOLDER_CHAT_TITLES = {
    "",
    "untitled chat",
    "conversation sans titre",
}


def is_placeholder_chat_title(title: str) -> bool:
    """Return True when title is an auto-placeholder and should be replaced."""
    return (title or "").strip().lower() in PLACEHOLDER_CHAT_TITLES


def enqueue_chat_title_generation(chat_id, language=None, timeout=600):
    """Queue chat title generation once per chat within a short lock window."""
    from django.db import transaction

    from otto.priorities import HIGH

    lock_key = f"chat_title_generation_{chat_id}"
    # cache.add returns True only if key did not already exist
    if not cache.add(lock_key, True, timeout=timeout):
        return False

    def _dispatch():
        try:
            from chat.tasks import generate_chat_title_task

            generate_chat_title_task.apply_async(
                args=[str(chat_id), language],
                priority=HIGH,
            )
        except Exception:
            cache.delete(lock_key)
            logger.exception("Failed to queue chat title generation", chat_id=chat_id)

    # Delay dispatch until the current transaction commits so the task sees
    # the messages that were just written. Falls back to immediate dispatch
    # when there is no active transaction (e.g. called outside a request).
    transaction.on_commit(_dispatch)
    return True


def annotate_pending_titles(chats, language=None):
    """
    For each chat in *chats*, set ``is_title_pending`` and display title, and
    enqueue async title generation when appropriate.
    Callers are responsible for setting ``current_chat`` separately.
    """
    for chat_obj in chats:
        if is_placeholder_chat_title(chat_obj.title):
            should_enqueue = (getattr(chat_obj, "message_count", 0) or 0) > 0
            chat_obj.is_title_pending = should_enqueue
            chat_obj.title = _("Untitled chat")
            if should_enqueue:
                enqueue_chat_title_generation(chat_obj.id, language=language)
        else:
            chat_obj.is_title_pending = False


def link_chat_files_to_library(files, user_message, data_source, priority=None):
    """
    Link ChatFile objects to the library as Documents, reusing existing documents
    by matching data_source, filename, and file hash.

    This ensures we don't create duplicate Documents when the same file is uploaded
    in different chat modes (Q&A, Summarize, Translate).

    Args:
        files: List of ChatFile objects
        user_message: Message object to associate with the documents
        data_source: DataSource to link the documents to
        priority: Priority for processing (default None uses task default)
    """
    from librarian.models import Document

    for file in files:
        # Skip if already linked to a document
        if hasattr(file, "document") and file.document:
            continue

        # Check for existing document by data_source, filename, and file hash
        existing_document = Document.objects.filter(
            data_source=data_source,
            filename=file.filename,
            saved_file__sha256_hash=file.saved_file.sha256_hash,
        ).first()

        if existing_document:
            # Reuse existing document
            if existing_document.status == "ERROR":
                # Retry processing if it previously failed
                if priority is not None:
                    existing_document.process(priority=priority)
                else:
                    existing_document.process()
            existing_document.messages.add(user_message)
            file.document = existing_document
            file.save()

            # If this is a container document (ZIP, MSG, EML), also associate child documents
            # Use parent_document relationship to find all children (works recursively for nested containers)
            child_documents = existing_document.child_documents.all()
            if child_documents.exists():
                for child_doc in child_documents:
                    child_doc.messages.add(user_message)
        else:
            # Create new document
            document = Document.objects.create(
                data_source=data_source,
                saved_file=file.saved_file,
                filename=file.filename,
            )
            document.messages.add(user_message)
            file.document = document
            file.save()
            # Queue processing
            if priority is not None:
                document.process(priority=priority)
            else:
                document.process()


def copy_options(source_options, target_options, user=None, chat=None, mode=None):
    # Check the source_options for deprecated models
    ChatOptions.objects.check_and_update_models(source_options)

    source_options_dict = model_to_dict(source_options)
    # Remove the fields that are not part of the preset
    for field in ["id", "chat"]:
        source_options_dict.pop(field, None)

    # Update the preset options with the dictionary
    fk_fields = [
        "qa_library",
        "translate_glossary",
    ]  # Include translate_glossary as FK
    m2m_fields = [
        "qa_data_sources",
        "qa_documents",
        "qa_additional_documents",
        "qa_excluded_documents",
    ]
    # Remove None values
    source_options_dict = {k: v for k, v in source_options_dict.items()}
    for key, value in source_options_dict.items():
        if key in fk_fields:
            setattr(target_options, f"{key}_id", int(value) if value else None)
        elif key in m2m_fields:
            getattr(target_options, key).set(value)
        else:
            setattr(target_options, key, value)

    request = get_request()
    user = user or (request and request.user)
    if user and (
        not target_options.qa_library
        or (
            user
            and not user.has_perm("librarian.view_library", target_options.qa_library)
        )
    ):
        if request:
            messages.warning(
                request,
                _(
                    "QA library for settings preset not accessible. It has been reset to your personal library."
                ),
            )
        if user.personal_library:
            target_options.qa_library = user.personal_library
            target_options.qa_data_sources.clear()
            target_options.qa_documents.clear()
            target_options.qa_additional_documents.clear()
            target_options.qa_excluded_documents.clear()
            target_options.qa_scope = "all"
            target_options.qa_mode = "rag"
    if chat:
        target_options.chat = chat
    if mode:
        target_options.mode = mode
    target_options.save()


def num_tokens_from_string(
    string: str, model: str = "gpt-4", enc_type: str = "o200k_base"
) -> int:
    """Returns the number of tokens in a text string."""
    string = string or ""
    try:
        encoding = tiktoken.get_encoding(enc_type)
        num_tokens = len(encoding.encode(string))
    except Exception:
        # Estimate the number of tokens using a simple heuristic (1 token = 4 chars)
        num_tokens = len(string) // 4
    return num_tokens


def wrap_llm_response(llm_response_str):
    return f'<div class="markdown-text" data-md="{html.escape(json.dumps(str(llm_response_str)))}"></div>'


def create_preset_shared_notification(
    recipient, preset, shared_by, can_edit: bool = False
):
    """
    Create a user notification when a preset is explicitly shared with them.

    Args:
        recipient: User receiving the notification
        preset: Preset being shared
        shared_by: User who shared the preset
        can_edit: Whether the recipient has edit permissions on the preset
    """

    if not recipient or not preset or not shared_by:
        return None

    # Avoid notifying the sharer about their own action
    if recipient == shared_by:
        return None

    shared_by_display = (
        getattr(shared_by, "full_name", None)
        or shared_by.get_full_name()
        or shared_by.username
    )
    preset_name_en = preset.name_en or preset.name_fr or "preset"
    preset_name_fr = preset.name_fr or preset.name_en or "préréglage"

    preset_id = preset.id
    chat_url = reverse("chat:from_preset", args=[preset_id])
    link_en = (
        f" <a class='alert-link' href='{chat_url}'>Start a new chat from the preset</a>"
    )
    link_fr = f" <a class='alert-link' href='{chat_url}'>Démarrer une nouvelle conversation à partir du préréglage</a>"

    if can_edit:
        text_en = (
            f'{shared_by_display} shared the AI Assistant preset "{preset_name_en}"'
            f" with you and granted edit access.{link_en}"
        )
        text_fr = (
            f'{shared_by_display} a partagé le préréglage de l\'assistant IA "{preset_name_fr}"'
            f" avec vous et vous a accordé l'accès en modification.{link_fr}"
        )
    else:
        text_en = (
            f'{shared_by_display} shared the AI Assistant preset "{preset_name_en}"'
            f" with you.{link_en}"
        )
        text_fr = (
            f'{shared_by_display} a partagé le préréglage de l\'assistant IA "{preset_name_fr}"'
            f" avec vous.{link_fr}"
        )

    return Notification.objects.create(
        user=recipient,
        heading_en="Preset shared",
        heading_fr="Préréglage partagé",
        text_en=text_en,
        text_fr=text_fr,
        category="info",
        link=chat_url,
    )


def create_library_shared_notification(recipient, library, shared_by, role: str):
    """
    Create a user notification when a library is explicitly shared with them.

    Args:
        recipient: User receiving the notification
        library: Library being shared
        shared_by: User who shared the library
        role: The role granted to the user ('admin', 'contributor', or 'viewer')
    """

    if not recipient or not library or not shared_by:
        return None

    # Avoid notifying the sharer about their own action
    if recipient == shared_by:
        return None

    shared_by_display = (
        getattr(shared_by, "full_name", None)
        or shared_by.get_full_name()
        or shared_by.username
    )
    library_name_en = library.name_en or library.name_fr or "library"
    library_name_fr = library.name_fr or library.name_en or "bibliothèque"

    library_id = library.id
    chat_url = f"/chat/?open_library={library_id}"
    onclick = f"if (typeof openLibrarianModal === 'function') {{ openLibrarianModal({library_id}, null); return false; }}"
    link_en = f" <a class='alert-link' href='{chat_url}' onclick=\"{onclick}\">View library</a>"
    link_fr = f" <a class='alert-link' href='{chat_url}' onclick=\"{onclick}\">Voir la bibliothèque</a>"

    text_en = (
        f'{shared_by_display} shared the Q&A library "{library_name_en}"'
        f" with you.{link_en}"
    )
    text_fr = (
        f'{shared_by_display} a partagé la bibliothèque de questions-réponses "{library_name_fr}"'
        f" avec vous.{link_fr}"
    )

    return Notification.objects.create(
        user=recipient,
        heading_en="Library shared",
        heading_fr="Bibliothèque partagée",
        text_en=text_en,
        text_fr=text_fr,
        category="info",
        link=chat_url,
    )


def create_skill_shared_notification(
    recipient,
    skill,
    shared_by,
    can_edit: bool = False,
    shared_via_team: bool = False,
    team_name: str | None = None,
):
    """
    Create a user notification when a skill is explicitly shared with them.

    Public visibility changes should not call this helper; it is only for direct
    user/team shares or edit-access upgrades.
    """

    if not recipient or not skill or not shared_by:
        return None

    if recipient == shared_by:
        return None

    shared_by_display = (
        getattr(shared_by, "full_name", None)
        or shared_by.get_full_name()
        or shared_by.username
    )
    skill_label = skill.display_name_en or skill.display_name_fr or str(skill)
    skill_name_en = (
        skill.display_name_en or skill.display_name_fr or skill_label or "skill"
    )
    skill_name_fr = (
        skill.display_name_fr or skill.display_name_en or skill_label or "compétence"
    )

    chat_url = f"{reverse('chat_next:new_chat')}?open_skill={skill.id}"
    link_en = f" <a class='alert-link' href='{chat_url}'>Open skill</a>"
    link_fr = f" <a class='alert-link' href='{chat_url}'>Ouvrir la compétence</a>"

    if shared_via_team and team_name:
        shared_phrase_en = (
            f'{shared_by_display} shared the AI Assistant skill "{skill_name_en}" '
            f'with your team "{team_name}"'
        )
        shared_phrase_fr = (
            f'{shared_by_display} a partagé la compétence de l\'assistant IA "{skill_name_fr}" '
            f'à votre équipe "{team_name}"'
        )
    elif shared_via_team:
        shared_phrase_en = (
            f'{shared_by_display} shared the AI Assistant skill "{skill_name_en}" '
            "with your team"
        )
        shared_phrase_fr = (
            f'{shared_by_display} a partagé la compétence de l\'assistant IA "{skill_name_fr}" '
            "avec votre équipe"
        )
    else:
        shared_phrase_en = f'{shared_by_display} shared the AI Assistant skill "{skill_name_en}" with you'
        shared_phrase_fr = f'{shared_by_display} a partagé la compétence de l\'assistant IA "{skill_name_fr}" avec vous'

    if can_edit:
        text_en = f"{shared_phrase_en} and granted edit access.{link_en}"
        text_fr = (
            f"{shared_phrase_fr} et vous a accordé l'accès en modification.{link_fr}"
        )
    else:
        text_en = f"{shared_phrase_en}.{link_en}"
        text_fr = f"{shared_phrase_fr}.{link_fr}"

    return Notification.objects.create(
        user=recipient,
        heading_en="Skill shared",
        heading_fr="Compétence partagée",
        text_en=text_en,
        text_fr=text_fr,
        category="info",
        link=chat_url,
    )


def format_reasoning_steps(
    reasoning_steps: list, include_incomplete: bool = False, language: str = None
) -> list:
    """
    Format reasoning steps from API into display format.
    Each step from API has 'index', 'text', and optionally 'complete'.
    We extract title and details from the text for better display.

    Args:
        reasoning_steps: List of step dicts from API
        include_incomplete: If False (default), only include complete steps OR
                          incomplete steps that have a complete first line (title).
                          If True, include all steps.
        language: Current user language code. If 'fr', appends a note that
                  reasoning is in English only.

    Returns list of dicts with 'title' and 'details' keys.
    """
    if not reasoning_steps:
        return []

    formatted_steps = []
    # Note for French users that reasoning is only in English
    french_suffix = " (raisonne exclusivement en anglais)" if language == "fr" else ""

    for step in reasoning_steps:
        is_complete = step.get("complete", True)
        text = step.get("text", "")
        if not text:
            continue

        # Check if we have a complete first line (title) by looking for newline
        has_complete_title = "\n" in text

        # Skip incomplete steps unless:
        # - explicitly requested via include_incomplete
        # - or we have a complete title line (show title early, even if details still streaming)
        if not include_incomplete and not is_complete and not has_complete_title:
            continue

        # Try to extract title from markdown header or first line
        lines = text.strip().split("\n")
        first_line = lines[0].strip()

        # Check if first line is a markdown header (starts with # or **)
        if first_line.startswith("#"):
            title = first_line.lstrip("#").strip()
            details = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        elif first_line.startswith("**") and first_line.endswith("**"):
            title = first_line.strip("*").strip()
            details = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        else:
            # Use first line as title (CSS handles truncation in collapsed view)
            title = first_line
            details = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

        formatted_steps.append(
            {
                "title": title + french_suffix,
                "details": details,
            }
        )

    return formatted_steps


async def stream_to_replacer(response_stream, attribute=None):
    response = ""
    try:
        async for chunk in response_stream:
            response += getattr(chunk, attribute) if attribute else chunk
            yield response
    except Exception:
        for chunk in response_stream:
            response += getattr(chunk, attribute) if attribute else chunk
            yield response
            await asyncio.sleep(0)  # This will allow other async tasks to run


def save_sources(source_nodes, message):
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
            except Exception:
                logger.error(
                    "Error saving source %s (%s)",
                    ref_doc_id=node.node.ref_doc_id,
                    node=node,
                )


def close_md_code_blocks(text):
    # Close any open code blocks
    if text.count("```") % 2 == 1:
        text += "\n```"
    elif text.count("`") % 2 == 1:
        text += "`"
    return text


def get_model_name(chat_options):
    """
    Get the model used for the chat message.
    """
    from chat._llm.models import get_chat_model_choices

    model_key = ""
    if chat_options.mode == "translate":
        if "gpt" in chat_options.translate_model:
            model_key = chat_options.translate_model
        elif chat_options.translate_model == "azure":
            return _("Azure Translator")
        elif chat_options.translate_model == "azure_custom":
            return _("Azure Translator - JUS custom")
    if chat_options.mode == "qa":
        model_key = chat_options.qa_model
    elif chat_options.mode == "chat":
        model_key = chat_options.chat_model
    elif chat_options.mode == "summarize":
        model_key = chat_options.summarize_model
    # chat_model_choices is a list of tuples
    # (model_key, model_description (including some stuff in parens))
    model_name = [
        model[1] for model in get_chat_model_choices() if model[0] == model_key
    ]
    if model_name:
        return model_name[0].split("(")[0].strip()
    else:
        return ""


async def htmx_stream(
    chat: Chat,
    message_id: int,
    llm: OttoLLM,
    response_generator: Generator = None,
    response_replacer: AsyncGenerator = None,
    response_str: str = "",
    wrap_markdown: bool = True,
    dots: bool = False,
    source_nodes: list = None,
    switch_mode: bool = False,
    remove_stop: bool = False,
    cost_warning_buttons: str = None,
    oob_html: str = None,
    query_info: list = None,
    stream_context: dict = None,
    keepalive_interval_seconds: float | None = SSE_KEEPALIVE_INTERVAL_SECONDS,
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

    query_info: Optional list of query info events to display in the reasoning widget.
                Each event should have 'title' and optional 'details' keys.
                Example: [{"title": "Optimizing search terms", "details": "query text"}]

    The response_replacer can yield dicts with special keys:
    - {"progress_events": [...]} - Events to add to the reasoning widget
    - {"source_nodes": [...]} - Source nodes to save with the message
    - {"text": "..."} - Text content (can be combined with above)
    """

    # Helper function to format a string as an SSE message
    def sse_string(
        message: str,
        wrap_markdown=True,
        dots=False,
        remove_stop=False,
        cost_warning_buttons=None,
        oob_html=None,
    ) -> str:
        sse_joiner = "\ndata: "
        if wrap_markdown:
            message = wrap_llm_response(message)
        if dots:
            message += dots
        out_string = "data: "
        out_string += sse_joiner.join(message.split("\n"))

        if cost_warning_buttons:
            # Render the form template asynchronously
            out_string += cost_warning_buttons

        if oob_html:
            # Add out-of-band HTML (e.g., accordion updates)
            out_string += sse_joiner + sse_joiner.join(oob_html.split("\n"))

        if remove_stop:
            out_string += sse_joiner + "<div hx-swap-oob='true' id='stop-button'></div>"
        out_string += "\n\n"  # End of SSE message
        return out_string

    ##############################
    # Start of the main function #
    ##############################
    # Initialize mutable defaults
    if source_nodes is None:
        source_nodes = []
    else:
        source_nodes = list(source_nodes)  # Make a copy to avoid mutating the original

    is_untitled_chat = is_placeholder_chat_title(chat.title)
    full_message = ""
    reasoning_steps = []  # Track structured reasoning steps from API
    has_reasoning = (
        False  # Track if we've ever had reasoning (persists after reasoning ends)
    )
    dots_html = '<div class="typing"><span></span><span></span><span></span></div>'
    # Track last sent reasoning JSON to avoid redundant OOB updates
    last_reasoning_json = ""
    # Track when text streaming has started - once true, stop reasoning OOB updates
    # This optimizes streaming by not re-rendering the widget during text generation
    text_started = False

    # If query_info is provided, show the reasoning widget
    if query_info:
        has_reasoning = True

    context = None  # Will be set by try or except block; checked in finally
    pending_response_task = None
    last_stream_render_at = 0.0
    stop_warning_message = _(
        "Response stopped early. Costs may still be incurred after stopping."
    )
    generation_stopped = False
    if dots:
        dots = dots_html
    if switch_mode:
        mode = chat.options.mode
        mode_str = {"qa": _("Q&A"), "chat": _("Chat")}[mode]
    stream_started_at = time.perf_counter()
    try:
        if response_generator:
            response_replacer = stream_to_replacer(response_generator)
        if response_str:
            response_replacer = stream_to_replacer([response_str])

        # Stream the response text
        first_message = True
        oob_html_sent = False
        response_iter = response_replacer.__aiter__()
        while True:
            if pending_response_task is None:
                pending_response_task = asyncio.create_task(response_iter.__anext__())

            try:
                if keepalive_interval_seconds and keepalive_interval_seconds > 0:
                    done, pending_tasks = await asyncio.wait(
                        {pending_response_task},
                        timeout=keepalive_interval_seconds,
                    )

                    if not done:
                        # Emit a harmless SSE comment so reverse proxies / browsers do not
                        # treat long model-thinking gaps as a dead connection.
                        yield ": keepalive\n\n"
                        continue

                    response = pending_response_task.result()
                else:
                    response = await pending_response_task
            except StopAsyncIteration:
                pending_response_task = None
                break

            pending_response_task = None
            if response is None:
                continue
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
                    oob_html=oob_html if not oob_html_sent else None,
                )
                oob_html_sent = True
                await asyncio.sleep(1)
                first_message = False

            # Handle both dict responses (with reasoning_steps) and string responses
            # Extract text content and check for batch boundary
            if isinstance(response, dict):
                # Check for progress_events (transient events for reasoning widget)
                # These REPLACE previous progress_events (like reasoning_steps from API)
                progress_events = response.get("progress_events", [])
                if progress_events:
                    query_info = progress_events
                    has_reasoning = True

                # Check for source_nodes from the generator
                response_source_nodes = response.get("source_nodes", [])
                if response_source_nodes:
                    source_nodes = source_nodes + response_source_nodes

                # New format from Responses API with reasoning
                text_response = response.get("text", "")
                api_reasoning_steps = response.get("reasoning_steps", [])
                is_reasoning = response.get("is_reasoning", False)
                is_batch_boundary = text_response == "<|batchboundary|>"
            else:
                # Original string format
                text_response = response
                api_reasoning_steps = []
                is_reasoning = False
                is_batch_boundary = response.endswith("<|batchboundary|>")

            if not is_batch_boundary:
                if remove_stop or not cache.get(f"stop_response_{message_id}", False):
                    full_message = text_response
                    # Handle reasoning steps (only for dict responses)
                    if api_reasoning_steps:
                        reasoning_steps = format_reasoning_steps(
                            api_reasoning_steps, language=get_language()
                        )
                        has_reasoning = True
                    if is_reasoning:
                        has_reasoning = True
                        full_message = ""  # Keep empty to trigger indicator
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

            # Prepare display message - reasoning will be handled separately via data-reasoning
            display_message = full_message

            # Check if we're in reasoning phase
            is_currently_reasoning = is_reasoning

            # Avoid overwhelming client with markdown rendering:
            # slow down yields if the message is large
            length = len(display_message)
            yield_every = length // 2000 + 1

            # Always yield during thinking phase, or when we have enough content
            should_yield = (
                has_reasoning  # Always yield if we have/had reasoning
                or length < 1000
                or length % yield_every == 0
            )

            should_throttle_render = (
                should_yield
                and display_message
                and not is_currently_reasoning
                and length >= SSE_RENDER_THROTTLE_MESSAGE_LENGTH
            )

            if should_throttle_render:
                now = time.monotonic()
                should_yield = (
                    last_stream_render_at == 0.0
                    or now - last_stream_render_at
                    >= SSE_RENDER_THROTTLE_INTERVAL_SECONDS
                )

            if should_yield:
                if should_throttle_render:
                    last_stream_render_at = time.monotonic()
                # Build complete HTML - message content only, reasoning data goes via OOB swap
                stream_html = ""

                # Build OOB update for reasoning data element (outside SSE swap area)
                # Only send when reasoning data actually changes to avoid redundant updates
                # that cause choppy streaming during text generation
                reasoning_oob = ""

                # Track when text streaming starts - after this, stop updating reasoning OOB
                # Once the final response begins, there are no more processing steps to show,
                # so we can stop the OOB swaps entirely.
                # Benefits: smaller payload, no re-renders, user can interact with collapse/expand
                if display_message and not text_started:
                    text_started = True

                # Only send reasoning OOB updates BEFORE text streaming starts
                # After text starts, the widget state is stable and doesn't need updates
                if has_reasoning and not text_started:
                    # Combine query_info events with reasoning_steps for display in widget
                    # query_info comes first (static), then reasoning_steps (from API)
                    all_events = (query_info or []) + reasoning_steps
                    reasoning_json = json.dumps(all_events, ensure_ascii=False)
                    # Only send OOB update if reasoning data has changed
                    if reasoning_json != last_reasoning_json:
                        last_reasoning_json = reasoning_json
                        # OOB swap updates the hidden data element, JS reads it and updates the widget
                        reasoning_oob = (
                            f'<div id="reasoning-data-{message_id}" hx-swap-oob="true" '
                            f'style="display:none" '
                            f'data-is-reasoning="{"true" if is_currently_reasoning else "false"}" '
                            f'data-reasoning="{html.escape(reasoning_json)}"></div>'
                        )

                # Add the message content (will be wrapped in markdown if wrap_markdown=True)
                # Note: We need to handle this carefully - if wrap_markdown is True, only wrap
                # the display_message part, not the reasoning widget
                if wrap_markdown and display_message:
                    stream_html += wrap_llm_response(display_message)
                elif display_message:
                    stream_html += display_message

                # During reasoning phase with no text yet, add typing dots
                # This ensures HTMX processes the SSE message and triggers OOB swaps
                if not display_message and has_reasoning and is_currently_reasoning:
                    stream_html = dots_html
                # Add dots if needed (outside the markdown wrapper)
                # But not if we're already showing dots for reasoning phase
                elif dots and not generation_stopped:
                    stream_html += dots

                # Combine reasoning OOB with other OOB HTML
                combined_oob = reasoning_oob
                if not oob_html_sent and oob_html:
                    combined_oob = (
                        (combined_oob + "\n" + oob_html) if combined_oob else oob_html
                    )

                yield sse_string(
                    stream_html,
                    wrap_markdown=False,  # Already wrapped above if needed
                    dots=False,  # Already added above if needed
                    remove_stop=remove_stop or generation_stopped,
                    oob_html=combined_oob if combined_oob else None,
                )
                oob_html_sent = True
            await asyncio.sleep(0.01)

        await sync_to_async(llm.create_costs)()

        message = await sync_to_async(Message.objects.get)(id=message_id)
        # Save the message text without thinking embedded
        finished_at = timezone.now()
        message.seconds_elapsed = (finished_at - message.date_created).total_seconds()
        message.details = {
            "is_granular": chat.options.qa_granular_toggle,
            "is_per_doc": chat.options.qa_process_mode == "per_doc",
        }
        # Store query_info in message details (for Q&A mode)
        if query_info:
            message.details["query_info"] = query_info
        # Store reasoning steps in message details (already formatted)
        if reasoning_steps:
            message.details["reasoning_steps"] = reasoning_steps

        # Save just the text response (reasoning will be displayed separately)
        message.text = full_message

        await sync_to_async(message.save)()

        if is_untitled_chat:
            title_llm = OttoLLM()
            await sync_to_async(title_chat)(chat.id, force_title=False, llm=title_llm)
            await sync_to_async(title_llm.create_costs)()
            await sync_to_async(message.chat.refresh_from_db)()

        # Save sources
        if source_nodes:
            await sync_to_async(save_sources)(source_nodes, message)

        # Refresh message from DB to get the saved text
        await sync_to_async(message.refresh_from_db)()

        # Combine query_info and reasoning_steps for display
        all_events = (message.details.get("query_info") or []) + (
            message.details.get("reasoning_steps") or []
        )

        stream_summary = {
            "stream_duration_ms": int((time.perf_counter() - stream_started_at) * 1000),
            "response_char_count": len(full_message or ""),
            "source_group_count": len(source_nodes or []),
            "had_reasoning": has_reasoning,
            "reasoning_step_count": len(reasoning_steps),
            "query_info_count": len(query_info or []),
            "generation_stopped": generation_stopped,
        }
        if stream_context:
            stream_summary.update(stream_context)

        logger.info("legacy_sse_stream_completed", **stream_summary)
        if stream_summary.get("workload_kind"):
            logger.info(
                "legacy_sse_request_classified",
                **classify_legacy_sse_stream(stream_summary),
            )

        context = {
            "message": message,
            "swap_oob": True,
            "update_cost_bar": True,
            "plain_message_text": full_message,
            # Pass JSON-encoded events (query_info + reasoning_steps) for script tag
            "reasoning_steps_json": (
                json.dumps(all_events, ensure_ascii=False) if all_events else None
            ),
        }

    except Exception as e:
        from otto.utils.common import generate_ai_error_summary

        try:
            message = await sync_to_async(Message.objects.get)(id=message_id)
        except Message.DoesNotExist:
            logger.warning("The message does not exist", message_id=message_id)
            return

        error_id = str(uuid.uuid4())[:7]
        full_message = await sync_to_async(generate_ai_error_summary)(e, error_id)

        logger.exception(
            "Error processing chat response",
            error_id=error_id,
            message_id=message.id,
            chat_id=chat.id,
        )
        message.text = full_message
        await sync_to_async(message.save)()
        context = {
            "message": message,
            "swap_oob": True,
            "plain_message_text": full_message,
        }
    finally:
        if pending_response_task and not pending_response_task.done():
            pending_response_task.cancel()

        # Always send the "done" event to signal client to close the SSE connection,
        # even if an unexpected error occurs. Without this, the browser may attempt
        # to reconnect and Daphne keeps the coroutine alive, leaking memory.
        try:
            if context:
                # Render the message template, wrapped in SSE format
                context["message"].json = json.dumps(str(context["message"].text))

                yield sse_string(
                    await sync_to_async(render_to_string)(
                        "chat/components/chat_message.html", context
                    ),
                    wrap_markdown=False,
                    remove_stop=True,
                    cost_warning_buttons=cost_warning_buttons,
                )
        except Exception:
            logger.exception("Error rendering final chat message")

        yield "event: done\ndata: complete\n\n"


def title_chat(chat_id, llm, force_title=True):
    # Assume costs will be calculated in the calling function where LLM instantiated
    chat = Chat.objects.select_related("user").get(id=chat_id)
    chat_messages = chat.messages.order_by("date_created")

    # Use .count() instead of len() to avoid loading all messages just for the count
    # This is more efficient as it uses SQL COUNT instead of fetching all rows
    message_count = chat_messages.count()

    # Treat "Untitled chat" (and French equivalent) as empty — it was a fallback
    # saved by the async task before meaningful content existed
    title_is_placeholder = is_placeholder_chat_title(chat.title)

    if not force_title and (
        not title_is_placeholder
        or (
            message_count < 5
            and len(" ".join([m.text for m in chat_messages[:5]])) < 1000
        )
    ):
        return chat.title

    chat_messages_text = []
    for message in chat_messages[:7]:
        if message.text:
            chat_messages_text.append(message.text)
        elif message.files.exists():
            chat_messages_text.append(message.mode + " " + _("with files:"))
            # Directly fetch filenames to avoid heavy joins
            filenames = message.files.values_list("filename", flat=True)
            for filename in filenames:
                chat_messages_text.append(filename)
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
    except Exception:
        generated_title = _("Untitled chat")
    chat.title = generated_title
    chat.save()
    return generated_title


async def summarize_chat_stream(llm, text, summarize_prompt="TL;DR:"):
    """
    Async generator that uses chat_stream for summarization.
    Properly handles reasoning model output by separating thinking from text.

    Yields dicts compatible with htmx_stream:
    - {"text": "...", "reasoning_steps": [...], "is_reasoning": bool}

    If the text is too long for the model's context window, yields an error message
    with suggestions for the user.
    """
    # Build the user message with document content and instructions
    if "{docs}" not in summarize_prompt:
        user_content = (
            "<document>\n"
            f"{text}\n"
            "</document>\n"
            "<instruction>\n"
            f"{summarize_prompt}\n"
            "</instruction>"
        )
    else:
        user_content = summarize_prompt.replace("{docs}", text)

    # Check if content exceeds context window before sending to LLM
    system_content = "You are a helpful assistant."
    total_input = system_content + user_content
    input_tokens = num_tokens_from_string(total_input)
    max_allowed = llm.max_input_tokens

    if input_tokens > max_allowed:
        error_message = _(
            "**Error:** This document is too long for the selected AI model.\n\n"
            "The document has approximately {input_tokens:,} tokens, but the model can only process about {max_allowed:,} tokens.\n\n"
            "**You can try:**\n"
            "1. Using a model with a larger context window (e.g., GPT-4.1 series supports up to 1M tokens)\n"
            "2. Splitting the document into smaller parts\n"
            "3. Summarizing a shorter excerpt of the document"
        ).format(input_tokens=input_tokens, max_allowed=max_allowed)
        yield {"text": error_message}
        return

    chat_history = [
        ChatMessage(role=MessageRole.SYSTEM, content=system_content),
        ChatMessage(role=MessageRole.USER, content=user_content),
    ]

    async for response in llm.chat_stream(chat_history):
        # chat_stream yields dicts for reasoning models, strings otherwise
        yield response


def summarize_long_text(
    text,
    llm,
    summarize_prompt="TL;DR:",
):
    """
    Returns an async generator for summarizing text using chat_stream.
    Properly handles reasoning model output.
    """
    return summarize_chat_stream(llm, text, summarize_prompt)


# Alias for backwards compatibility - both return an async generator
summarize_long_text_async = summarize_long_text


def get_source_titles(sources):
    return [
        source.metadata.get("title", source.metadata["source"]) for source in sources
    ]


def create_batches(iterable, n=1):
    length = len(iterable)
    for ndx in range(0, length, n):
        yield iterable[ndx : min(ndx + n, length)]


async def combine_response_replacers(generators, titles):
    """
    Combine multiple response replacer generators into one batch.

    Each generator can yield either:
    - Strings (legacy format)
    - Dicts with 'text' key (new format from qa_chat_stream for reasoning models)

    For single document: yields {"text": ...} during streaming (actual content)
    For multiple documents: yields {"streaming": True, ...} during processing,
    then {"final_text": ...} when complete.
    """
    import uuid as uuid_module

    # Single document case: stream directly without batching overhead
    if len(generators) == 1:
        formatted_title = f"\n###### *{titles[0]}*\n"
        final_text = ""
        try:
            async for response in generators[0]:
                if isinstance(response, dict):
                    text = response.get("text", "")
                    is_reasoning = response.get("is_reasoning", False)
                else:
                    text = response
                    is_reasoning = False

                # Only update final_text if not in reasoning phase
                if not is_reasoning:
                    final_text = text

                # Yield actual content during streaming
                # Use final_text (not text) to avoid character count drops during reasoning
                yield_dict = {"text": formatted_title + final_text}
                # Pass through reasoning info if present
                if isinstance(response, dict):
                    if "reasoning_steps" in response:
                        yield_dict["reasoning_steps"] = response["reasoning_steps"]
                    if "is_reasoning" in response:
                        yield_dict["is_reasoning"] = response["is_reasoning"]
                yield yield_dict
                await asyncio.sleep(0)
        except Exception as e:
            error_id = str(uuid_module.uuid4())[:7]
            logger.exception(
                "Error in combine_response_replacers",
                error_id=error_id,
                title=titles[0],
            )
            error_str = str(e).lower()
            if "context" in error_str or "token" in error_str or "length" in error_str:
                error_message = _(
                    "This document may be too long for the AI model. "
                    "Try using a model with a larger context window or splitting the document."
                )
            else:
                error_message = _("Error generating response.")
            error_id_label = _("Error ID:")
            final_text = f"{error_message} _({error_id_label} {error_id})_"
            yield {"text": formatted_title + final_text}
        # Yield final metadata for consistency
        logger.info(
            "combine_response_batch_completed",
            doc_count=1,
            title_count=1,
            total_chars=len(final_text),
        )
        yield {
            "final_text": formatted_title + final_text,
            "char_count": len(final_text),
            "doc_count": 1,
        }
        return

    # Multiple documents case
    streams = [{"stream": stream, "status": "running"} for stream in generators]
    formatted_titles = [f"\n###### *{title}*\n" for title in titles]
    final_streams = ["" for _ in titles]

    while any([stream["status"] == "running" for stream in streams]):
        for i, stream in enumerate(streams):
            try:
                if stream["status"] == "running":
                    response = await stream["stream"].__anext__()
                    # Handle both dict and string responses
                    if isinstance(response, dict):
                        text = response.get("text", "")
                        is_reasoning = response.get("is_reasoning", False)
                    else:
                        text = response
                        is_reasoning = False

                    # Only update final_streams if not in reasoning phase
                    if not is_reasoning:
                        final_streams[i] = text
            except StopAsyncIteration:
                stream["status"] = "stopped"
            except Exception as e:
                error_id = str(uuid_module.uuid4())[:7]
                logger.exception(
                    "Error in combine_response_replacers",
                    error_id=error_id,
                    title=titles[i] if i < len(titles) else "unknown",
                )
                # Check for common error types and provide more helpful messages
                error_str = str(e).lower()
                if (
                    "context" in error_str
                    or "token" in error_str
                    or "length" in error_str
                ):
                    error_message = _(
                        "This document may be too long for the AI model. "
                        "Try using a model with a larger context window or splitting the document."
                    )
                else:
                    error_message = _("Error generating response.")
                error_id_label = _("Error ID:")
                final_streams[i] = f"{error_message} _({error_id_label} {error_id})_"
                stream["status"] = "stopped"

        # Signal that we're still streaming (parent will generate status message)
        batch_completed = sum(1 for s in streams if s["status"] == "stopped")
        batch_chars = sum(len(s) for s in final_streams)
        yield {
            "streaming": True,
            "batch_completed": batch_completed,
            "batch_total": len(titles),
            "batch_chars": batch_chars,
        }
        await asyncio.sleep(0)

    # All streams complete - yield the combined final text with metadata
    combined_text = "\n\n---\n\n".join(
        [formatted_titles[i] + final_streams[i] for i in range(len(titles))]
    )
    total_chars = sum(len(s) for s in final_streams)
    logger.info(
        "combine_response_batch_completed",
        doc_count=len(titles),
        title_count=len(titles),
        total_chars=total_chars,
    )
    yield {
        "final_text": combined_text,
        "char_count": total_chars,
        "doc_count": len(titles),
    }


async def combine_batch_generators(batch_generators, total_count=None):
    """
    Combine multiple batch generators into one for htmx_stream.

    Args:
        batch_generators: List of generators from combine_response_replacers
        total_count: Total number of documents across all batches (optional, inferred if not provided)

    For single document: passes through text yields directly (normal streaming)
    For multiple documents:
    - Yields throttled status messages during processing
    - Accumulates final text from completed batches
    - Yields full combined text after all batches complete
    """
    import time

    # Single document case: pass through directly without status messages
    if total_count == 1:
        async for response in batch_generators[0]:
            if isinstance(response, dict):
                if "text" in response:
                    yield response  # Pass through streaming text
                elif "final_text" in response:
                    yield {"text": response["final_text"]}
            await asyncio.sleep(0)
        yield "<|batchboundary|>"
        return

    # Multiple documents case
    final_streams = []
    total_chars = 0
    completed_docs = 0
    last_yield_time = 0
    yield_interval = 0.5  # Only yield status updates every 500ms

    # If total_count not provided, we can't show accurate progress until we process
    # For now, show "Processing..." and update as we learn more
    known_total = total_count

    for batch_idx, generator in enumerate(batch_generators):
        async for response in generator:
            if isinstance(response, dict):
                if "final_text" in response:
                    # Batch complete - accumulate
                    batch_final_text = response["final_text"]
                    total_chars += response.get("char_count", 0)
                    completed_docs += response.get("doc_count", 0)
                    final_streams.append(batch_final_text)
                elif response.get("streaming"):
                    # Still processing - yield status message (throttled)
                    current_time = time.monotonic()
                    if current_time - last_yield_time >= yield_interval:
                        last_yield_time = current_time
                        batch_completed = response.get("batch_completed", 0)
                        batch_chars = response.get("batch_chars", 0)
                        current_chars = total_chars + batch_chars
                        current_completed = completed_docs + batch_completed

                        # Extract translations outside f-string for xgettext
                        processing = _("Processing")
                        complete = _("documents complete")
                        chars_so_far = _("characters generated so far")
                        if known_total:
                            status_msg = f"{processing} ({current_completed}/{known_total} {complete}; {current_chars:,} {chars_so_far})..."
                        else:
                            status_msg = f"{processing} ({current_completed} {complete}; {current_chars:,} {chars_so_far})..."
                        yield {"text": status_msg}
            await asyncio.sleep(0)

        yield "<|batchboundary|>"

    # All batches complete - yield the final combined text
    if final_streams:
        logger.info(
            "combine_batch_stream_completed",
            batch_count=len(batch_generators),
            total_document_count=total_count,
            total_chars=total_chars,
        )
        yield {"text": "\n\n---\n\n".join(final_streams)}


def group_sources_into_docs(source_nodes):
    def doc_key(x):
        return x.node.ref_doc_id

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


def update_qa_library_for_chat_uploads(chat):
    """
    Update Q&A library settings to "Chat Uploads" (user's personal library)
    without changing the current mode. Returns accordion HTML for swap.
    """
    chat.options.qa_library = chat.user.personal_library
    chat.options.qa_scope = "data_sources"
    chat.options.qa_data_sources.set([chat.data_source])
    chat.options.qa_additional_documents.clear()
    chat.options.qa_excluded_documents.clear()
    chat.options.save()

    request = get_request()
    # Ensure the request has chat_id in GET params so autocomplete can identify "This chat"
    if request:
        # Make a mutable copy of GET params
        request.GET = request.GET.copy()
        request.GET["chat_id"] = str(chat.id)

    return render_to_string(
        "chat/components/chat_options_accordion.html",
        {
            "options_form": ChatOptionsForm(instance=chat.options, user=chat.user),
            "mode": chat.options.mode,
            "swap": "true",
            "chat": chat,
        },
        request=request,
    )


def change_mode_to_chat_qa(chat):
    """
    Change mode to Q&A and update library settings to "Chat Uploads".
    Returns accordion HTML for swap.
    """
    chat.options.mode = "qa"
    return update_qa_library_for_chat_uploads(chat)


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
    llm = OttoLLM(deployment="gpt-4.1-mini")

    META_PROMPT = """
    Given a current prompt and a change description, produce a detailed system prompt to guide a language model in completing the task effectively.
    
    Answer in whatever language you are asked. If the input is in French, generate the output in French. If the input is in English, generate the output in English. Do not translate the input text, but respond in the same language as the input.
    
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

    [Additional details as needed, translate the subtitles below starting with "#" too, such as "# Output Format".]

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


def mark_sentences(text: str, good_matches: list) -> str:
    """
    Ignoring "\n" and "\r" characters in the text, wrap matching sentences in the text with <mark> tags.
    Return the original text with the sentences wrapped in <mark> tags, with original newlines preserved.
    """
    # Replace newline characters with temporary markers.
    newline_marker = "<<<NEWLINE>>>"
    text_temp = text.replace("\n", newline_marker).replace("\r", "")

    good_matches = set(good_matches)

    # For each sentence that should be marked, search and wrap it.
    for sentence in good_matches:
        # Remove leading/trailing whitespace and escape regex-special characters.
        sentence_clean = sentence.strip()
        # Escape regex special characters.
        escaped = re.escape(sentence_clean)
        # Replace literal spaces (escaped as "\ ") with a pattern that allows matching spaces or newline markers.
        flexible_pattern = escaped.replace(
            r"\ ",
            r"(?:\s|" + re.escape(newline_marker) + r"+|" + r")+",
        )
        pattern = re.compile(flexible_pattern, flags=re.IGNORECASE)
        # Wrap any match with <mark> tags.
        text_temp = pattern.sub(r"<mark>\g<0></mark>", text_temp)

    # Restore original newlines.
    marked_text = text_temp.replace(newline_marker, "\n")
    # If there are sections where a mark spans over multiple paragraphs, we must highlight them all.
    # e.g. <mark>paragraph 1\n\nparagraph 2</mark> -> <mark>paragraph 1</mark>\n\n<mark>paragraph 2</mark>
    marked_text = re.sub(
        r"<mark>(.*?)\n\n(.*?)</mark>",
        r"<mark>\1</mark>\n\n<mark>\2</mark>",
        marked_text,
    )
    # Remove nested <mark> tags
    marked_text = re.sub(r"<mark>(.*?)<mark>", r"<mark>\1", marked_text)
    marked_text = re.sub(r"</mark></mark>", r"</mark>", marked_text)
    return marked_text


def highlight_claims(claims_list, text, threshold=0.66):
    """
    Highlight sentences in text with <mark> that match a claim in the claims_list.
    """
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

    # print("SENTENCES:")
    # for sentence in sentences:
    #     print(sentence)
    # print("CLAIMS:")
    # for claim in claims_list:
    #     print(claim)

    good_matches = []
    for claim in claims_list:
        retriever = index.as_retriever()
        nodes = retriever.retrieve(claim)
        # print("CLAIM:", claim)
        # print("matches:")
        # print([(node.score, node.node.text) for node in nodes])
        # print("\n")
        for node in nodes:
            if node.score > threshold:
                good_matches.append(node.text)

    text = mark_sentences(text, good_matches)
    return text


def extract_claims_from_llm(llm_response_text):
    llm = OttoLLM()
    prompt = f"""
    Based on the following LLM response, extract all factual claims including direct quotes.

    Respond in the format:
    <claim>whatever the claim is...</claim>
    <claim>another claim...</claim>

    etc.
    Include all factual claims as their own sentence. Do not include any analysis or reasoning.

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


@cache_within_request
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
        except Exception:
            continue

    return text


@cache_within_request
def label_section_index(last_modification_date):
    last_modification_date = last_modification_date.date()
    todays_date = timezone.now().date()
    if last_modification_date > todays_date - timezone.timedelta(days=1):
        return 1
    elif last_modification_date > todays_date - timezone.timedelta(days=2):
        return 2
    elif last_modification_date > todays_date - timezone.timedelta(days=7):
        return 3
    elif last_modification_date > todays_date - timezone.timedelta(days=30):
        return 4
    else:
        return 5


def get_chat_history_sections(user_chats):
    """
    Group the chat history into sections formatted as [{"label": "(string)", "chats": [list..]}]
    """
    chat_history_sections = [
        {"label": _("Pinned chats"), "chats": []},
        {"label": _("Today"), "chats": []},
        {"label": _("Yesterday"), "chats": []},
        {"label": _("Last 7 days"), "chats": []},
        {"label": _("Last 30 days"), "chats": []},
        {"label": _("Older"), "chats": []},
    ]

    for user_chat in user_chats:
        if user_chat.pinned:
            chat_history_sections[0]["chats"].append(user_chat)
            continue
        section_index = label_section_index(user_chat.last_modification_date)
        chat_history_sections[section_index]["chats"].append(user_chat)

    return chat_history_sections


def is_text_to_summarize(message):
    return message.mode == "summarize" and not message.is_bot


# MIME types supported for vision models
IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
}

# File types supported in Chat mode (vision models)
# Images and PDFs can be passed directly to vision-capable models
CHAT_MODE_SUPPORTED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".pdf",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def is_image_file(chat_file):
    """Check if a ChatFile represents an image based on content_type or filename."""
    if chat_file.saved_file and chat_file.saved_file.content_type:
        return chat_file.saved_file.content_type.lower() in IMAGE_MIME_TYPES

    # Fallback to filename extension
    filename_lower = chat_file.filename.lower()
    return any(
        filename_lower.endswith(ext)
        for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]
    )


def is_pdf_file(chat_file):
    """Check if a ChatFile represents a PDF based on content_type or filename."""
    if chat_file.saved_file and chat_file.saved_file.content_type:
        return chat_file.saved_file.content_type.lower() == "application/pdf"
    return chat_file.filename.lower().endswith(".pdf")


def is_image_filename(filename):
    """Check if a filename has an image extension."""
    filename_lower = filename.lower()
    return any(filename_lower.endswith(ext) for ext in IMAGE_EXTENSIONS)


def is_pdf_filename(filename):
    """Check if a filename has a PDF extension."""
    return filename.lower().endswith(".pdf")


def is_vision_supported_file(filename):
    """Check if a filename has an extension supported by vision models in Chat mode."""
    filename_lower = filename.lower()
    return any(filename_lower.endswith(ext) for ext in CHAT_MODE_SUPPORTED_EXTENSIONS)


def get_vision_blocks_for_message(message, include_images=True, include_pdfs=True):
    """
    Extract ImageBlock and DocumentBlock objects for files attached to a message.

    Args:
        message: Message object to extract files from
        include_images: If True, include image files (PNG, JPG, GIF, etc.)
        include_pdfs: If True, include PDF files

    Returns:
        List of ImageBlock/DocumentBlock objects for vision-supported files
    """
    blocks = []

    if not hasattr(message, "files") or not message.files.exists():
        return blocks

    for chat_file in message.files.all():
        saved_file = chat_file.saved_file
        if not saved_file or not saved_file.file:
            continue

        try:
            if include_images and is_image_file(chat_file):
                # Use ImageBlock for images - read bytes directly
                with saved_file.file.open("rb") as f:
                    file_bytes = f.read()
                mime_type = saved_file.content_type or "image/png"
                blocks.append(
                    ImageBlock(
                        image=file_bytes,
                        image_mimetype=mime_type,
                        detail="high",
                    )
                )
            elif include_pdfs and is_pdf_file(chat_file):
                # Use DocumentBlock for PDFs - use path instead of data
                # This is a workaround for a LlamaIndex bug where _guess_mimetype()
                # fails on base64-encoded data but works with file paths
                blocks.append(
                    DocumentBlock(
                        path=saved_file.file.path,
                        document_mimetype="application/pdf",
                        title=chat_file.filename,
                    )
                )
        except Exception as e:
            logger.warning(
                "Failed to load file for chat message",
                filename=chat_file.filename,
                error=str(e),
            )

    return blocks


# Keep old function name as alias for backwards compatibility
def get_image_blocks_for_message(message):
    """Deprecated: Use get_vision_blocks_for_message instead."""
    return get_vision_blocks_for_message(message)


def chat_to_history(
    chat,
    system_prompt=None,
    filter_mode=None,
    skip_empty=True,
    include_files=False,  # Vision mode disabled - always False
):
    """
    Convert a Chat object to a history list of LlamaIndex ChatMessage objects.

    Args:
        chat: Chat object to convert
        system_prompt: Optional custom system prompt. If None, uses chat.options.chat_system_prompt
        filter_mode: Optional mode to filter messages by (e.g., 'qa', 'chat'). If None, includes all modes
        skip_empty: If True, skip messages with no text content
        include_files: DISABLED - Vision mode is disabled, files always go through Q&A mode

    Returns:
        List of ChatMessage objects
    """
    # Vision mode disabled - ignore include_files parameter and chat options
    # include_files = False

    if system_prompt is None:
        # Build system prompt (chat_file_capabilities_prompt returns empty string now)
        system_prompt = (
            current_time_prompt() + chat.options.chat_system_prompt
            # + chat_file_capabilities_prompt(False, False)
        )

    history = []
    history.append(ChatMessage(role=MessageRole.SYSTEM, content=system_prompt))

    # Get messages queryset with optional filtering
    messages_qs = chat.messages.all()
    if filter_mode:
        messages_qs = messages_qs.filter(mode=filter_mode)
    messages_qs = messages_qs.order_by("date_created")

    for message in messages_qs:
        role = MessageRole.ASSISTANT if message.is_bot else MessageRole.USER

        # Vision blocks disabled - always empty
        vision_blocks = []

        # Determine message content
        if not message.text or message.text.strip() == "":
            # Try to get filenames from files
            filenames = []
            if hasattr(message, "files") and message.files.exists():
                filenames = [f.filename for f in message.files.all()]
            if filenames:
                if message.is_bot:
                    content = _("Bot responded with these files: ") + ", ".join(
                        filenames
                    )
                else:
                    content = _("User uploaded these files: ") + ", ".join(filenames)
            else:
                # Only skip if there's no text AND no files AND no vision blocks
                if skip_empty and not vision_blocks:
                    continue
                content = _("(empty message)") if not vision_blocks else ""
        elif is_text_to_summarize(message):
            content = _("<text to summarize...>")
        else:
            content = message.text

        # Build the ChatMessage with blocks if we have vision content (images/PDFs)
        if vision_blocks:
            blocks = []
            # Add text block first if there's text content
            if content:
                blocks.append(TextBlock(text=str(content)))
            # Add vision blocks (images, PDFs)
            blocks.extend(vision_blocks)
            history.append(ChatMessage(role=role, blocks=blocks))
        else:
            history.append(ChatMessage(role=role, content=content))

    # Remove trailing empty assistant message if present
    if (
        history
        and history[-1].role == MessageRole.ASSISTANT
        and not history[-1].content
        and not history[-1].blocks
    ):
        history.pop()

    return history


def qa_to_history(chat, response_message):
    """
    Convert a Chat object to a history list for Q&A mode.
    Includes all message types (chat, Q&A, summarize, translate).
    Uses the QA system prompt instead of chat system prompt.
    Skips empty messages (including the blank response being generated).
    """
    system_prompt = current_time_prompt() + chat.options.qa_system_prompt

    return chat_to_history(
        chat,
        system_prompt=system_prompt,
        filter_mode=None,
        skip_empty=True,
    )


def translate_text_with_azure(text, target_language, custom_translator_id=None):
    """
    Translate text using Azure Text Translation service.
    Returns the translated text.
    """
    from azure.ai.translation.text import TextTranslationClient
    from azure.core.credentials import AzureKeyCredential
    from azure.core.exceptions import HttpResponseError

    try:
        # Map language codes to Azure Translator format
        language_mapping = {"en": "en", "fr": "fr-ca"}  # Use Canadian French

        target_lang = language_mapping.get(target_language, target_language)

        # Create translation client
        credential = AzureKeyCredential(settings.AZURE_AI_SERVICES_KEY)
        text_translator = TextTranslationClient(
            credential=credential, endpoint=settings.AZURE_AI_SERVICES_ENDPOINT
        )

        # Translate the text
        response = text_translator.translate(
            body=[text], to_language=[target_lang], category=custom_translator_id
        )

        if response and len(response) > 0:
            translation = response[0]
            if translation.translations and len(translation.translations) > 0:
                translated_text = translation.translations[0].text

                # Track usage for cost calculation
                char_count = len(text)
                cost_type = (
                    "translate-custom" if custom_translator_id else "translate-text"
                )
                Cost.objects.new(cost_type=cost_type, count=char_count)

                return translated_text

        raise Exception("No translation received from Azure Translator")

    except HttpResponseError as exception:
        logger.exception(f"Azure AI Services Translator API error: {exception}")
        if exception.error is not None:
            raise Exception(
                f"Azure AI Services Translator Error: {exception.error.message}"
            )
        raise Exception("Azure AI Services Translator API error")
    except Exception as e:
        logger.exception(f"Error translating text with Azure AI Services: {e}")
        raise Exception(f"Translation failed: {str(e)}")


def swap_glossary_columns(file):
    """
    Given a glossary CSV file, swap the first two columns and return a new file-like object.
    """
    import csv
    import io

    file.seek(0)
    reader = csv.reader(io.StringIO(file.read().decode("utf-8")))
    output = io.StringIO()
    writer = csv.writer(output)

    for row in reader:
        if len(row) >= 2:
            row[0], row[1] = row[1], row[0]
        writer.writerow(row)

    output.seek(0)
    return io.BytesIO(output.read().encode("utf-8"))


def cost_warning_response(chat, response_message, estimate_cost, over_budget=False):
    formatted_cost = f"{estimate_cost:.2f}"
    if over_budget:
        cost_warning = _(
            f"This request is estimated to cost ${formatted_cost}, which exceeds your remaining monthly budget. Please contact an Otto administrator or wait until the 1st for the limit to reset."
        )
    else:
        cost_warning = _("This request could be expensive. Are you sure?")

    cost_warning_buttons = render_to_string(
        "chat/components/cost_warning_buttons.html",
        {
            "message_id": response_message.id,
            "estimate_cost": formatted_cost,
            "continue_button": not over_budget,
        },
    ).replace("\n", "")

    llm = OttoLLM()

    return StreamingHttpResponse(
        streaming_content=htmx_stream(
            chat,
            response_message.id,
            llm,
            response_str=cost_warning,
            cost_warning_buttons=cost_warning_buttons,
        ),
        content_type="text/event-stream",
    )


def generate_cost_warning(chat, response_message, skip_cost=False, request=None):
    """Return a cost warning response when the estimated request cost is too high."""
    user = chat.user
    estimate_cost = estimate_cost_of_request(chat, response_message)

    # Check which budget to use: cost group or personal
    active_cost_group = request and user.get_active_cost_group(request)

    if active_cost_group:
        # Check cost group budget
        cost_group_cost_this_month = cad_cost(
            Cost.objects.get_cost_group_cost_this_month(active_cost_group)
        )
        this_month_max = active_cost_group.monthly_max

        if (estimate_cost + cost_group_cost_this_month) >= this_month_max:
            return cost_warning_response(
                chat,
                response_message,
                estimate_cost,
                over_budget=True,
            )
        if (estimate_cost >= settings.WARN_COST) and not skip_cost:
            return cost_warning_response(chat, response_message, estimate_cost)
    else:
        # Check personal budget
        user_cost_this_month = cad_cost(Cost.objects.get_user_cost_this_month(user))
        this_month_max = user.this_month_max

        if (estimate_cost + user_cost_this_month) >= this_month_max:
            return cost_warning_response(
                chat,
                response_message,
                estimate_cost,
                over_budget=True,
            )
        if (estimate_cost >= settings.WARN_COST) and not skip_cost:
            return cost_warning_response(chat, response_message, estimate_cost)

    return None


def options_match(chat_options, preset_options):
    """
    Compare chat options to preset options to determine if they match.
    Returns True if all comparable fields match, False otherwise.

    This function compares field-by-field to avoid serialization issues
    that caused false positives in previous implementations.

    """
    if not chat_options or not preset_options:
        return False

    # Fields to compare - these are the user-configurable options
    # Excludes: id, chat (FK), prompt (saved separately), qa_library_id (permission-dependent),
    # translate_glossary_id (user-specific), and M2M fields (context-dependent)
    simple_fields = [
        "mode",
        # Chat options
        "chat_model",
        "chat_temperature",
        "chat_reasoning_effort",
        "chat_verbosity",
        "chat_system_prompt",
        "chat_include_images",
        "chat_include_pdfs",
        # Summarize options
        "summarize_model",
        "summarize_reasoning_effort",
        "summarize_verbosity",
        "summarize_prompt",
        # Translate options
        "translate_language",
        "translate_model",
        "translate_prompt",
        # QA options (excluding qa_library_id - see docstring)
        "qa_model",
        "qa_reasoning_effort",
        "qa_verbosity",
        "qa_mode",
        "qa_process_mode",
        "qa_scope",
        "qa_topk",
        "qa_system_prompt",
        "qa_pre_instructions",
        "qa_post_instructions",
        "qa_source_order",
        "qa_vector_ratio",
        "qa_granular_toggle",
        "qa_granularity",
        "qa_history",
    ]

    for field in simple_fields:
        chat_val = getattr(chat_options, field, None)
        preset_val = getattr(preset_options, field, None)

        # Normalize empty strings and None to be equivalent
        if chat_val in (None, ""):
            chat_val = ""
        if preset_val in (None, ""):
            preset_val = ""

        # For strings, normalize line endings and strip whitespace before comparing
        # Browser submits \r\n (CRLF) but database stores \n (LF)
        if isinstance(chat_val, str):
            chat_val = chat_val.replace("\r\n", "\n").replace("\r", "\n").strip()
        if isinstance(preset_val, str):
            preset_val = preset_val.replace("\r\n", "\n").replace("\r", "\n").strip()

        # For floats, compare with tolerance
        if isinstance(chat_val, float) and isinstance(preset_val, float):
            if abs(chat_val - preset_val) > 0.0001:
                return False
        elif chat_val != preset_val:
            return False

    # Compare FK fields by ID
    def id_or_none(opts, attr):
        return getattr(opts, f"{attr}_id", None)

    if id_or_none(chat_options, "translate_glossary") != id_or_none(
        preset_options, "translate_glossary"
    ):
        return False

    # Compare qa_library by ID (ForeignKey, not M2M)
    if id_or_none(chat_options, "qa_library") != id_or_none(
        preset_options, "qa_library"
    ):
        return False

    # Compare M2M fields by set of IDs
    # Use .all() instead of .values_list() to leverage Django's prefetch cache
    # when the data has already been prefetched by the view.
    def ids_set(opts, attr):
        try:
            return {obj.id for obj in getattr(opts, attr).all()}
        except Exception:
            return set()

    if ids_set(chat_options, "qa_data_sources") != ids_set(
        preset_options, "qa_data_sources"
    ):
        return False

    if ids_set(chat_options, "qa_documents") != ids_set(preset_options, "qa_documents"):
        return False

    if ids_set(chat_options, "qa_additional_documents") != ids_set(
        preset_options, "qa_additional_documents"
    ):
        return False

    if ids_set(chat_options, "qa_excluded_documents") != ids_set(
        preset_options, "qa_excluded_documents"
    ):
        return False

    return True
