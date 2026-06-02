from datetime import datetime
from typing import Callable

from chat_next._utils.context_hints import (
    is_valid_tool_context_hint_id,
    sanitize_runtime_context_hints,
)


def _prompt_block(title: str, bullets: list[str]) -> str:
    """Format a concise prompt block with bullet-point guidance."""
    return (
        "\n---\n"
        + title
        + "\n"
        + "\n".join(f"- {bullet}" for bullet in bullets)
        + "\n---\n"
    )


def _short_text(value: str | None, max_len: int = 120) -> str:
    """Normalize whitespace and keep only a compact leading summary."""
    text = " ".join(str(value or "").split())
    if not text:
        return ""

    for sep in (". ", "; ", "\n"):
        if sep in text:
            candidate = text.split(sep, 1)[0].strip()
            if len(candidate) >= max_len:
                text = candidate
                break

    # if len(text) <= max_len:
    return text
    # return text[: max_len - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def current_time_prompt():
    return "Date: " + datetime.now().strftime("%Y-%m-%d") + "\n"


def known_resource_ids_prompt(
    *,
    corporate_library_id: int | None = None,
    chat_files_library_id: int | None = None,
    current_chat_data_source_id: int | None = None,
) -> str:
    """Provide runtime resource IDs the model can reuse for tool calls."""

    parts = []
    if corporate_library_id is not None:
        parts.append(f"Corporate library ID [{corporate_library_id}]")
    if chat_files_library_id is not None:
        parts.append(f"Chat files library ID [{chat_files_library_id}]")
    if current_chat_data_source_id is not None:
        parts.append(f"This chat data source ID [{current_chat_data_source_id}]")

    if not parts:
        return ""

    return "\n---\nCOMMON RESOURCE IDS:\n" + "; ".join(parts) + "\n---\n"


def context_awareness_prompt(
    model_id: str, max_tokens_in: int, context_pct: int | None = None
) -> str:
    """
    Build a prompt section informing the model about its own context window,
    possible history lossiness, and current usage level.

    Args:
        model_id: The active model ID (e.g. "gpt-5.1")
        max_tokens_in: The model's maximum input token capacity
        context_pct: Percentage of context window currently used (0-100),
                     or None if unknown (first message in chat)
    """
    parts = [f"\n---\nCONTEXT: {model_id}, ~{max_tokens_in // 1000}K tokens\n"]

    if context_pct is not None:
        parts.append(f"Usage: ~{context_pct}%\n")

    parts.append(
        "Older turns may already be summarized or truncated.\n"
        "- If earlier tool state seems incomplete, re-run the tool instead of guessing.\n"
        "- Near the limit, prefer smaller reads/outputs.\n"
        "---\n"
    )
    return "".join(parts)


# ============================================================================
# Source Transparency Prompt
# ============================================================================


def source_transparency_prompt():
    """Mandatory rules for transparent attribution of information sources."""
    return _prompt_block(
        "SOURCE TRANSPARENCY (MANDATORY):",
        [
            "Label tool-sourced claims whenever possible including filename and page numbers.",
            "If a claim is from training data only, say so and never imply a live query confirmed it.",
            "For current or high-stakes facts, prefer approved tools or clearly qualify the answer.",
            "If tools fail, say you could not verify the information.",
            "Never expose internal IDs (e.g. document_id) in user-facing text.",
        ],
    )


# ============================================================================
# Tool System Prompts
# ============================================================================
# Each tool can have an associated system prompt providing usage guidance.
# These are NOT translated — the model performs best with English instructions.
#
# To add a prompt for a new tool:
# 1. Define a function that returns the prompt string
# 2. Register it in TOOL_PROMPTS with the tool ID as key
def _code_interpreter_prompt():
    """Instructions for code interpreter tool usage."""
    return _prompt_block(
        "CODE INTERPRETER:",
        [
            "Use it for computation, data analysis, charts, conversions, and programmatic file manipulation.",
            "Generated files are saved and shared with the user automatically.",
            "Do not use it for reading or summarizing document text; use `get_document_text` and answer directly.",
            "Use `load_library_files` when Python needs raw files in `/mnt/data/`.",
            "If using matplotlib, call `matplotlib.use('Agg')` before importing `matplotlib.pyplot`.",
        ],
    )


def _otto_functions_prompt():
    """Instructions for Q&A library tools (document reading, search)."""
    return _prompt_block(
        "DOCUMENT TOOLS:",
        [
            "`rag_search` only searches embedded chunks; documents with status `PAUSED` are not searchable there.",
            "Use explicit IDs from `list_libraries`, `list_folders`, or `list_documents`.",
            "To narrow search to one or more folders/documents, use `data_source_ids` or `document_ids` (single-element lists are fine).",
            "`PAUSED` documents can still be used with `get_document_text`, `find_in_document`, `load_library_files`, or `view_library_files`.",
            "For single-document summaries, reviews, or comprehensive analysis, prefer `get_document_text` over semantic search or quote-hunting.",
            "When summarizing one document, start at the beginning and keep reads contiguous unless the user asked for specific sections.",
            "If one document needs multiple reads, prefer a few large continuation reads over many selective snippets.",
            "Default workflow is search → read: use `start_char` or `page_number` from search results directly, and skip extra reading if the excerpt already answers the question.",
            "Use `find_in_document` only for exact text positions, counts, or pattern-matching.",
            "Use `get_document_text` as the default path for reading, summaries, and quotes; only use `view_library_files` for visual verification when needed.",
            "When `get_document_text` returns `VISION_RECOMMENDED`, run targeted `view_library_files` on the minimum relevant page range (`start_page`/`end_page`) instead of loading full documents.",
            "Call `view_library_files` when layout matters or when scan-only content (handwriting, signatures, checkboxes, unreliable tables/diagrams) must be interpreted.",
            "If visual review is needed on large/32MB+ PDFs, keep requests narrow and page-targeted; avoid whole-document visual loads.",
            "Do not resolve OCR ambiguity by paraphrasing or by choosing between plausible readings without visual verification.",
            "If OCR may matter and the document is roughly 50 pages or fewer, use targeted visual verification.",
            "`top_k` can be as high as 200, but each chunk may be ~768 tokens, so start around 5-10 and increase only when the extra recall is worth the context.",
            "For ordered multi-document reads, prefer one `get_document_text` call with `document_ids`; `<page_N>` tags are the canonical PDF page numbers.",
        ],
    ) + _prompt_block(
        "DOCUMENT COVERAGE:",
        [
            "`get_document_text` returns a COVERAGE object; you MUST report what you actually read, including chars/pages and approximate percentage when relevant.",
            "If you started mid-document or used multiple calls, say which ranges/pages you read; never imply you started at the beginning unless you did.",
            "If coverage is partial, say so and offer to continue; `COVERAGE.MORE_TO_READ` tells you how.",
            "Never say you read the full document unless `coverage_pct` is 100%.",
        ],
    )


def _local_document_processing_prompt():
    """Instructions for document-processing tools that can either stream inline or create files."""
    return _prompt_block(
        "DOCUMENT PROCESSING TOOL ROUTING:",
        [
            "Use `prompt_documents` for whole-document per-file outputs such as 'summarize each file' or front-matter tasks such as titles, authors, or dates. Each run sees ONE source document only.",
            "For cross-document work, use `prompt_documents` as a map stage, then inspect the outputs yourself for the final comparison or synthesis in chat.",
            "Explicitly set `llm_model` to `gpt-5.4-nano` by default for routine document-processing calls; use `gpt-5.4-mini` only when materially stronger reasoning or better writing is clearly needed.",
            "Use `prompt_document_chunks` for one very large document (about >=400K characters); it is map-only, and large chunks such as `target_chars=400000` are preferred unless finer granularity is truly needed.",
            "After `prompt_document_chunks`, read ALL returned chunk outputs before making claims about the whole document; prefer one ordered `get_document_text` call.",
            "For one document that is not very large (<400K characters), prefer `get_document_text` + an inline answer unless the user explicitly wants a file output.",
            "Do not rely on semantic search as the main method for exhaustive multi-document ranking or comparison questions; inspect generated outputs and do the final comparison in chat.",
            "After processing, keep the user-facing response brief and file-first. If you used `truncate_chars`, say only the first N characters were processed.",
        ],
    )


def _local_legal_research_prompt():
    """Instructions for the A2AJ-backed local legal research tools."""
    return _prompt_block(
        "CANADIAN LEGAL RESEARCH TOOL:",
        [
            "Use A2AJ for Canadian case law (including provincial/territorial courts and tribunals) and for legislation/regulations available through A2AJ.",
            "Route requests with `list_canadian_legal_datasets`, `search_canadian_case_law`, `fetch_canadian_case_by_citation`, `search_canadian_legislation`, and `fetch_canadian_legislation_by_citation`.",
            "If the user asks what datasets A2AJ has or which dataset codes to use, call `list_canadian_legal_datasets` first.",
            "A2AJ search supports AND/OR/NOT, ET/OU/NON, quoted phrases, parentheses, wildcard suffixes, proximity such as privacy NEAR/5 workplace, and French `EXACT(...)`.",
            "Default sort uses relevance plus A2AJ dataset boosting unless the user explicitly wants newest or oldest results.",
            "Use search results as a screening pass before opening many full decisions or laws.",
            "For first-pass case screening, the default fetch window is ~25000 chars; prefer one larger follow-up or `end_char=-1` over many small slices.",
            "Cite A2AJ briefly in the answer and do NOT use TERMIUM for case law.",
            "Keep outbound A2AJ queries minimal and warn the user if the outbound text appears sensitive.",
        ],
    )


def _termium_prompt():
    """Instructions for TERMIUM Plus terminology tool."""
    return _prompt_block(
        "TERMIUM PLUS TERMINOLOGY TOOL:",
        [
            "`termium_lookup` is ONLY for bilingual or multilingual terminology lookups, official Government of Canada term translations, and usage notes.",
            "Do NOT use it for legal research, case law, legislation, current events, public-fact lookups, office holders, biographies, organizations, or webpage reading.",
            "`termium_lookup` returns terminology records, NOT legal content.",
            "Send only short sanitized terminology queries and warn the user if the outbound text appears sensitive.",
        ],
    )


def _url_retriever_prompt():
    """Instructions for URL retriever tool."""
    return _prompt_block(
        "URL RETRIEVAL:",
        [
            "Treat download/retrieve/get URL requests as instructions to add that content to this chat's uploads library.",
            "If extracted text is not enough, use other file tools on the ingested file.",
            "Follow relevant links recursively when needed.",
            "This tool can retrieve webpages and non-HTML content.",
        ],
    )


# Registry mapping tool IDs to their prompt functions
# Keys must match tool IDs from chat_next.models.AVAILABLE_TOOLS
TOOL_PROMPTS: dict[str, Callable[[], str]] = {
    "code_interpreter": _code_interpreter_prompt,
    "local_qa_libraries": _otto_functions_prompt,
    "local_legal_research": _local_legal_research_prompt,
    "local_document_processing": _local_document_processing_prompt,
    # "local_transcription": ...,  # Not yet approved
    "local_terminology": _termium_prompt,
    "url_retriever": _url_retriever_prompt,
}


def skill_metadata_prompt(available_skills) -> str:
    """Build compact metadata for available skills.

    Only names + descriptions — bodies loaded on demand.
    """
    if not available_skills:
        return ""

    lines = [
        "\nSKILLS:\n",
        "When the user's request matches any of these skills, call `load_skill_instructions` "
        "with the skill id(s) IMMEDIATELY before other instructions or calls.\n",
    ]
    for skill in available_skills:
        display = skill.display_name_en or skill.display_name
        desc = skill.description_en or skill.description
        lines.append(f"- {display} [id={skill.id}]: {_short_text(desc, 60)}\n")

    return "".join(lines)


def _get_latest_context_hints(chat) -> list[dict]:
    """Return context hints from the latest user message in the chat."""
    if chat is None:
        return []

    last_user_msg = chat.messages.filter(is_bot=False).order_by("-date_created").first()
    if not last_user_msg:
        return []

    hints = (last_user_msg.details or {}).get("context_hints") or []
    return sanitize_runtime_context_hints(hints)


def get_effective_available_skills(chat_settings, *, chat=None, user=None) -> list:
    """Return enabled skills plus accessible one-turn hinted skills.

    Context-hinted skills should be available to the model for the current turn
    without being persisted into ``enabled_skills``.
    """
    from chat_next.models import Skill

    target_user = (
        user or getattr(chat_settings, "user", None) or getattr(chat, "user", None)
    )
    if not target_user:
        return []

    available_skills = list(chat_settings.get_accessible_enabled_skills(target_user))
    if chat is None:
        return available_skills

    hinted_skill_ids = []
    for hint in _get_latest_context_hints(chat):
        if hint.get("type") != "skill":
            continue
        hint_id = str(hint.get("id", ""))
        if hint_id.isdigit():
            hinted_skill_ids.append(int(hint_id))

    if not hinted_skill_ids:
        return available_skills

    skills_by_id = {skill.pk: skill for skill in available_skills}
    hinted_skills = {
        skill.pk: skill
        for skill in Skill.objects.get_accessible(target_user).filter(
            pk__in=hinted_skill_ids,
        )
    }
    for skill_id in hinted_skill_ids:
        skill = hinted_skills.get(skill_id)
        if skill and skill.pk not in skills_by_id:
            available_skills.append(skill)
            skills_by_id[skill.pk] = skill

    return available_skills


def get_available_skill_tool_ids(chat_settings, *, chat=None, user=None) -> list[str]:
    """Return valid tool categories declared by skills available for this turn.

    This is used for request-manifest construction only. Prompt guidance stays
    progressive and is added elsewhere.
    """
    tool_ids: list[str] = []

    for skill in get_effective_available_skills(chat_settings, chat=chat, user=user):
        for tool_id in skill.required_tools or []:
            normalized_tool_id = str(tool_id or "").strip()
            if (
                is_valid_tool_context_hint_id(normalized_tool_id)
                and normalized_tool_id not in tool_ids
            ):
                tool_ids.append(normalized_tool_id)

        for hint in sanitize_runtime_context_hints(skill.context_hints or []):
            if hint.get("type") != "tool" or not hint.get("id"):
                continue

            tool_id = hint["id"]
            if tool_id not in tool_ids:
                tool_ids.append(tool_id)

    return tool_ids


def get_persisted_loaded_skill_tool_ids(chat=None) -> list[str]:
    """Return persisted tool-category IDs unlocked earlier in this chat."""
    if chat is None:
        return []

    state = getattr(chat, "loaded_skill_state", None) or {}
    tool_ids = []
    for raw_tool_id in state.get("tool_ids") or []:
        normalized_tool_id = str(raw_tool_id or "").strip()
        if (
            is_valid_tool_context_hint_id(normalized_tool_id)
            and normalized_tool_id not in tool_ids
        ):
            tool_ids.append(normalized_tool_id)
    return tool_ids


def get_effective_enabled_tools(
    chat_settings,
    *,
    chat=None,
    user=None,
    include_available_skill_tools: bool = False,
) -> list[str]:
    """Return tool categories to expose for the current request context.

    By default this returns the prompt-safe active tool list: user-enabled
    tools, the hidden ``local_skills`` loader category, and any valid per-
    message tool hints selected for the current turn.

    When ``include_available_skill_tools`` is true, valid tool categories
    declared by currently available skills are also merged in so the outgoing
    Responses API manifest already contains those tool definitions before
    ``load_skill_instructions`` runs.
    """
    from chat_next.models import TOOL_CATEGORY_SKILLS, sanitize_enabled_tools

    enabled = list(sanitize_enabled_tools(chat_settings.chat_enabled_tools))
    # local_skills should always be available at runtime even though it's hidden in UI.
    if TOOL_CATEGORY_SKILLS not in enabled:
        enabled.append(TOOL_CATEGORY_SKILLS)

    if include_available_skill_tools:
        for tool_id in get_available_skill_tool_ids(
            chat_settings,
            chat=chat,
            user=user,
        ):
            if tool_id not in enabled:
                enabled.append(tool_id)

    for tool_id in get_persisted_loaded_skill_tool_ids(chat):
        if tool_id not in enabled:
            enabled.append(tool_id)

    # Merge in valid tools from per-message context hints so the API receives
    # definitions for every tool the user explicitly selected for this turn.
    if chat is not None:
        for hint in _get_latest_context_hints(chat):
            if hint.get("type") == "tool" and hint.get("id"):
                tool_id = hint["id"]
                if tool_id not in enabled:
                    enabled.append(tool_id)

    return enabled


def get_tool_prompts(enabled_tools: list[str]) -> str:
    """
    Get combined system prompt text for all enabled tools.

    Args:
        enabled_tools: List of tool IDs that are currently enabled

    Returns:
        Combined prompt string for all enabled tools that have prompts
    """
    prompts = []
    for tool_id in enabled_tools:
        if tool_id in TOOL_PROMPTS:
            prompt_fn = TOOL_PROMPTS[tool_id]
            prompts.append(str(prompt_fn()))
    return "".join(prompts)
