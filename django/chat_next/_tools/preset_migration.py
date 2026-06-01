"""Preset migration tools for chat_next."""

from asgiref.sync import sync_to_async
from structlog import get_logger

from chat_next._tools.base import TOOL_REGISTRY, OttoTool, ToolContext
from chat_next._utils.context_hints import is_valid_tool_context_hint_id
from chat_next.models import TOOL_CATEGORY_SKILLS, Skill

logger = get_logger(__name__)


def _build_skill_link_result(skill: Skill) -> dict:
    """Return standard skill-open link metadata for tool responses."""
    label = skill.display_name_en or skill.display_name_fr or str(skill)
    return {
        "skill_id": skill.id,
        "skill_name": label,
        "display_name_en": skill.display_name_en,
        "display_name_fr": skill.display_name_fr,
        "edit_url": f"skill://{skill.id}",
        "edit_link_token": f"[[OPEN_SKILL:{skill.id}|{label}]]",
    }


def _is_authenticated_user(user, chat=None):
    """Permission check: any authenticated user may use skill-management tools."""
    return user is not None and user.is_authenticated


def _get_migratable_presets_queryset(user):
    """Return presets visible for migration for this user.

    Source of truth for migration visibility:
    - Admin users: can include System presets
    - Non-admin users: only non-System presets they can access
    """
    from chat.models import Preset

    presets = Preset.objects.get_accessible_presets(user)
    if not user.is_admin:
        presets = presets.exclude(owner__isnull=True)
    return presets


def _normalize_context_hints(context_hints):
    """Normalize and validate context hints for stable ID-based access checks.

    - library/document hint IDs are stored as numeric strings
    - for library hints, a non-numeric id may be resolved from exact library name
    """
    from librarian.models import Library

    if not context_hints:
        return [], None

    normalized = []
    for hint in context_hints:
        if not isinstance(hint, dict):
            return None, "Each context hint must be an object."

        hint_type = hint.get("type")
        hint_id = hint.get("id")
        hint_name = hint.get("name", "")

        if hint_type not in {"tool", "library", "document"}:
            return None, f"Unsupported context hint type: {hint_type}"

        if hint_id is None:
            return None, f"Context hint '{hint_name or hint_type}' is missing an id."

        # Tool IDs are string category IDs (e.g., local_qa_libraries)
        if hint_type == "tool":
            tool_id = str(hint_id).strip()
            if not is_valid_tool_context_hint_id(tool_id):
                return (
                    None,
                    "Unsupported tool context hint id "
                    f"'{hint_id}'. Tool context hints must use high-level tool "
                    "category ids such as local_qa_libraries, "
                    "local_document_processing, local_legal_research, "
                    "local_terminology, url_retriever, code_interpreter, or "
                    "local_skills. Mention granular tool names like "
                    "get_document_text or create_skill in the skill body "
                    "instructions instead of context_hints.",
                )
            normalized.append(
                {
                    "type": "tool",
                    "id": tool_id,
                    "name": hint_name,
                }
            )
            continue

        # library/document IDs must be numeric for rules-based resolution
        raw_id = str(hint_id).strip()
        numeric_id = None
        if raw_id.isdigit():
            numeric_id = int(raw_id)
        elif hint_type == "library" and hint_name:
            # Backward-compatible rescue path: if model used library name in id,
            # resolve exact (case-insensitive) name to an ID.
            matches = list(
                Library.objects.filter(name__iexact=hint_name)
                .order_by("id")
                .values_list("id", flat=True)[:2]
            )
            if len(matches) == 1:
                numeric_id = matches[0]
            elif len(matches) > 1:
                return (
                    None,
                    f"Library hint '{hint_name}' is ambiguous. Use a numeric library id.",
                )
            else:
                return (
                    None,
                    f"Library hint '{hint_name}' could not be resolved to an id.",
                )

        if numeric_id is None:
            return (
                None,
                f"{hint_type.title()} context hints must use numeric ids. Got id='{hint_id}'.",
            )

        normalized.append(
            {
                "type": hint_type,
                "id": str(numeric_id),
                "name": hint_name,
            }
        )

    return normalized, None


# ---------------------------------------------------------------------------
# list_presets
# ---------------------------------------------------------------------------


async def _list_presets(arguments: dict, context: ToolContext) -> dict:
    """List presets visible for migration to the current user."""
    user = context.user

    def _fetch():
        presets = _get_migratable_presets_queryset(user)
        results = []
        for p in presets:
            results.append(
                {
                    "id": p.id,
                    "name_en": p.name_en,
                    "name_fr": p.name_fr,
                    "description_en": p.description_en or "",
                    "description_fr": p.description_fr or "",
                    "mode": p.options.mode,
                    "owner": str(p.owner) if p.owner else "System",
                    "is_default": p.english_default or p.french_default,
                    "sharing": p.sharing_option,
                }
            )
        return results

    presets = await sync_to_async(_fetch)()

    if not presets:
        return {
            "success": True,
            "result": {"presets": [], "note": "No presets found for this user."},
        }

    return {"success": True, "result": {"presets": presets}}


TOOL_REGISTRY.register(
    OttoTool(
        name="list_presets",
        description=(
            "List legacy AI Assistant presets visible for migration for the current user."
            # "Admins may see System presets; non-admin users do not. "
            # "Returns preset ID, name, description, and mode. "
            # "Use this to help the user choose which preset to migrate to a Skill."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        execute=_list_presets,
        category=TOOL_CATEGORY_SKILLS,
        requires_user=True,
        requires_chat=False,
        permission_check=_is_authenticated_user,
        strict=True,
    )
)


# ---------------------------------------------------------------------------
# read_preset
# ---------------------------------------------------------------------------

# Fields relevant to each mode, so we don't dump irrelevant config
_MODE_FIELDS = {
    "chat": [
        "chat_model",
        "chat_temperature",
        "chat_reasoning_effort",
        "chat_verbosity",
        "chat_system_prompt",
        "chat_include_images",
        "chat_include_pdfs",
    ],
    "qa": [
        "qa_model",
        "qa_reasoning_effort",
        "qa_verbosity",
        "qa_library",
        "qa_mode",
        "qa_process_mode",
        "qa_scope",
        "qa_topk",
        "qa_system_prompt",
        "qa_prompt_template",
        "qa_pre_instructions",
        "qa_post_instructions",
        "qa_source_order",
        "qa_vector_ratio",
        "qa_granular_toggle",
        "qa_granularity",
        "qa_history",
    ],
    "summarize": [
        "summarize_model",
        "summarize_reasoning_effort",
        "summarize_verbosity",
        "summarize_prompt",
    ],
    "translate": [
        "translate_language",
        "translate_model",
        "translate_glossary_filename",
        "translate_prompt",
    ],
}

# Fields that are always included regardless of mode
_COMMON_FIELDS = ["mode", "prompt"]


async def _read_preset(arguments: dict, context: ToolContext) -> dict:
    """Read a preset's ChatOptions for a preset visible in list_presets."""
    user = context.user
    preset_id = arguments["preset_id"]

    def _fetch():
        from chat.models import Preset

        try:
            preset = Preset.objects.select_related(
                "options", "options__qa_library", "owner"
            ).get(id=preset_id, is_deleted=False)
        except Preset.DoesNotExist:
            return None, "Preset not found."

        # Check user has access according to migration visibility rules
        accessible = _get_migratable_presets_queryset(user).filter(id=preset_id)
        if not accessible.exists():
            return None, "You don't have access to this preset."

        opts = preset.options
        mode = opts.mode

        # Build output with common fields + mode-specific fields
        result = {}
        for field_name in _COMMON_FIELDS:
            result[field_name] = getattr(opts, field_name, "")

        mode_fields = _MODE_FIELDS.get(mode, _MODE_FIELDS["chat"])
        for field_name in mode_fields:
            val = getattr(opts, field_name, None)
            if field_name == "qa_library" and val is not None:
                result["qa_library_id"] = val.id
                result["qa_library_name"] = val.name
            elif field_name == "qa_data_sources":
                # Skip M2M in simple serialization
                pass
            elif field_name == "qa_documents":
                pass
            else:
                result[field_name] = val

        # Add preset metadata
        preset_info = {
            "preset_id": preset.id,
            "name_en": preset.name_en,
            "name_fr": preset.name_fr,
            "description_en": preset.description_en,
            "description_fr": preset.description_fr,
            "owner": str(preset.owner) if preset.owner else "System",
            "is_default": preset.english_default or preset.french_default,
        }

        return {"preset": preset_info, "options": result}, None

    data, error = await sync_to_async(_fetch)()

    if error:
        return {"success": False, "error": error}

    return {"success": True, "result": data}


TOOL_REGISTRY.register(
    OttoTool(
        name="read_preset",
        description=(
            "Read the configuration details of a specific legacy preset by ID. "
            # "Returns only the ChatOptions fields relevant to the preset's mode "
            # "(chat, qa, summarize, or translate), omitting unrelated settings. "
            "Use this after list_presets to inspect a preset before migration."
        ),
        parameters={
            "type": "object",
            "properties": {
                "preset_id": {
                    "type": "integer",
                    "description": "",
                },
            },
            "required": ["preset_id"],
            "additionalProperties": False,
        },
        execute=_read_preset,
        category=TOOL_CATEGORY_SKILLS,
        requires_user=True,
        requires_chat=False,
        permission_check=_is_authenticated_user,
        strict=True,
    )
)


async def _create_skill(arguments: dict, context: ToolContext) -> dict:
    """Create a new Skill from provided data."""
    user = context.user

    display_name_en = arguments.get("display_name_en", "")
    display_name_fr = arguments.get("display_name_fr", "")
    description_en = arguments.get("description_en", "")
    description_fr = arguments.get("description_fr", "")
    body_en = arguments.get("body_en", "")
    body_fr = arguments.get("body_fr", "")
    context_hints = arguments.get("context_hints", [])

    if not display_name_en and not display_name_fr:
        return {"success": False, "error": "At least one display name is required."}

    if not body_en and not body_fr:
        return {
            "success": False,
            "error": "At least one body (instructions) is required.",
        }

    normalized_hints, hints_error = await sync_to_async(_normalize_context_hints)(
        context_hints
    )
    if hints_error:
        return {"success": False, "error": hints_error}

    def _do_create():
        skill = Skill.objects.create(
            display_name_en=display_name_en,
            display_name_fr=display_name_fr,
            description_en=description_en,
            description_fr=description_fr,
            body_en=body_en,
            body_fr=body_fr,
            context_hints=normalized_hints,
            sharing_option="private",
            owner=user,
        )

        # Auto-enable for user
        from chat_next.models import ChatSettings

        settings_obj, _ = ChatSettings.objects.get_or_create_for_user(user)
        settings_obj.enabled_skills.add(skill)

        return {
            **_build_skill_link_result(skill),
            "sharing_option": skill.sharing_option,
            "note": (
                "Skill created as private and is now enabled. "
                "To use it, select it from the context picker (the @ button "
                "in the chat input). To edit it, open the Skills browser "
                "(lightbulb icon in the sidebar) or use the edit link."
            ),
        }

    result = await sync_to_async(_do_create)()
    return {"success": True, "result": result}


TOOL_REGISTRY.register(
    OttoTool(
        name="create_skill",
        description="Create a new AI Assistant Skill.",
        # Provide display names, descriptions, "
        # "body instructions (markdown), and optional context hints. "
        # "The skill will be owned by the current user and auto-enabled.",
        parameters={
            "type": "object",
            "properties": {
                "display_name_en": {
                    "type": "string",
                    "description": "English display name for the skill.",
                },
                "display_name_fr": {
                    "type": "string",
                    "description": "French display name for the skill.",
                },
                "description_en": {
                    "type": "string",
                    "description": (
                        "English trigger description: what the skill does and when to use it."
                        # "what does this skill do "
                        # "and when should the AI use it."
                    ),
                },
                "description_fr": {
                    "type": "string",
                    "description": "French trigger description.",
                },
                "body_en": {
                    "type": "string",
                    "description": (
                        "English markdown instructions (the skill body/detailed guidance)."
                        # "This is the detailed guidance the AI follows."
                    ),
                },
                "body_fr": {
                    "type": "string",
                    "description": "French markdown instructions.",
                },
                "context_hints": {
                    "type": "array",
                    "description": (
                        "Context hints: tools and documents the skill needs."
                        # "Tool hints must use top-level tool category ids only "
                        # "(for example local_qa_libraries), not granular function "
                        # "names like get_document_text or create_skill. "
                        # 'Each item has "type" (tool/library/document), '
                        # '"id" (tool category or resource ID), and "name".'
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["tool", "library", "document"],
                            },
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                        },
                        "required": ["type", "id", "name"],
                        "additionalProperties": False,
                    },
                },
                "sharing_option": {
                    "type": "string",
                    "enum": ["private", "everyone", "others"],
                    "description": (
                        "Ignored by this tool since skills are always created as private."
                        # "share later in the Skills window."
                    ),
                },
            },
            "required": [
                "display_name_en",
                "description_en",
                "body_en",
            ],
            "additionalProperties": False,
        },
        execute=_create_skill,
        category=TOOL_CATEGORY_SKILLS,
        requires_user=True,
        requires_chat=False,
        permission_check=_is_authenticated_user,
        strict=False,
    )
)


# ---------------------------------------------------------------------------
# edit_skill
# ---------------------------------------------------------------------------


async def _edit_skill(arguments: dict, context: ToolContext) -> dict:
    """Edit an existing Skill."""
    user = context.user
    skill_id = arguments["skill_id"]

    normalized_hints = None
    if "context_hints" in arguments and arguments["context_hints"] is not None:
        normalized_hints, hints_error = await sync_to_async(_normalize_context_hints)(
            arguments["context_hints"]
        )
        if hints_error:
            return {"success": False, "error": hints_error}

    def _do_edit():
        try:
            skill = Skill.objects.get(id=skill_id)
        except Skill.DoesNotExist:
            return None, "Skill not found."

        # Check edit permission
        can_edit = (
            skill.owner == user or user in skill.editable_by.all() or user.is_admin
        )
        if not can_edit:
            return None, "You don't have permission to edit this skill."

        # Update only provided fields
        updatable = [
            "display_name_en",
            "display_name_fr",
            "description_en",
            "description_fr",
            "body_en",
            "body_fr",
            "context_hints",
        ]
        changed = []
        for field in updatable:
            if field in arguments and arguments[field] is not None:
                value = arguments[field]
                if field == "context_hints" and normalized_hints is not None:
                    value = normalized_hints
                setattr(skill, field, value)
                changed.append(field)

        if changed:
            skill.save()

        return {
            **_build_skill_link_result(skill),
            "updated_fields": changed,
            "display_name_en": skill.display_name_en,
            "display_name_fr": skill.display_name_fr,
            "sharing_option": skill.sharing_option,
            "note": (
                "Sharing remains private when editing via this tool. "
                "Use the Skills window to review, test, enable or share after editing."
            ),
        }, None

    result, error = await sync_to_async(_do_edit)()

    if error:
        return {"success": False, "error": error}

    return {"success": True, "result": result}


TOOL_REGISTRY.register(
    OttoTool(
        name="edit_skill",
        description=(
            "Edit an existing AI Assistant Skill."
            # "Provide the skill ID and any "
            # "fields to update. Only provided fields will be changed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "integer",
                    "description": "The ID of the skill to edit.",
                },
                "display_name_en": {
                    "type": "string",
                    "description": "Updated English display name.",
                },
                "display_name_fr": {
                    "type": "string",
                    "description": "Updated French display name.",
                },
                "description_en": {
                    "type": "string",
                    "description": "Updated English trigger description.",
                },
                "description_fr": {
                    "type": "string",
                    "description": "Updated French trigger description.",
                },
                "body_en": {
                    "type": "string",
                    "description": "Updated English markdown instructions.",
                },
                "body_fr": {
                    "type": "string",
                    "description": "Updated French markdown instructions.",
                },
                "context_hints": {
                    "type": "array",
                    "description": (
                        "Updated context hints array."
                        # "Tool hints must use top-level "
                        # "tool category ids only (for example local_qa_libraries), "
                        # "not granular function names like get_document_text or "
                        # "edit_skill."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["tool", "library", "document"],
                            },
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                        },
                        "required": ["type", "id", "name"],
                        "additionalProperties": False,
                    },
                },
                "sharing_option": {
                    "type": "string",
                    "enum": ["private", "everyone", "others"],
                    "description": (
                        "Ignored by this tool since sharing remains private."
                        # "use the Skills window to share."
                    ),
                },
            },
            "required": ["skill_id"],
            "additionalProperties": False,
        },
        execute=_edit_skill,
        category=TOOL_CATEGORY_SKILLS,
        requires_user=True,
        requires_chat=False,
        requires_approval=True,
        permission_check=_is_authenticated_user,
        strict=False,
    )
)


# ---------------------------------------------------------------------------
# create_skill_from_preset  (deterministic migration)
# ---------------------------------------------------------------------------


def _build_chat_body(opts):
    """Build skill body for a chat-mode preset."""
    parts = []
    if opts.prompt:
        parts.append(opts.prompt.strip())
    if opts.chat_system_prompt:
        parts.append(opts.chat_system_prompt.strip())
    extras = []
    if getattr(opts, "chat_include_images", False):
        extras.append("When the user uploads images, analyze them visually.")
    if getattr(opts, "chat_include_pdfs", False):
        extras.append("When the user uploads PDFs, read and analyze their content.")
    verbosity = getattr(opts, "chat_verbosity", "")
    if verbosity == "low":
        extras.append("Keep responses concise.")
    elif verbosity == "high":
        extras.append("Provide thorough, detailed responses.")
    if extras:
        parts.append("\n".join(extras))
    return "\n\n".join(parts) if parts else ""


def _build_qa_body(opts):
    """Build skill body for a Q&A-mode preset.

    Copies the user's original prompts verbatim and adds deterministic
    search instructions based on config (library, scope, mode, etc.).
    """
    parts = []

    # --- User's original prompts (verbatim) ---
    if opts.qa_system_prompt:
        parts.append(opts.qa_system_prompt.strip())

    if opts.qa_pre_instructions:
        parts.append("## Answer instructions\n\n" + opts.qa_pre_instructions.strip())

    if opts.qa_post_instructions:
        parts.append("## Response format\n\n" + opts.qa_post_instructions.strip())

    # --- Deterministic search guidance ---
    search_lines = ["## Search configuration\n"]

    # Library / scope
    lib = opts.qa_library
    lib_id = lib.id if lib else None
    lib_name = lib.name if lib else None

    scope = opts.qa_scope or "all"
    topk = opts.qa_topk or 5

    if scope == "all" and lib_id:
        search_lines.append(
            f"Search the **{lib_name}** library for relevant information "
            f"using `rag_search(library_id={lib_id}, query=..., top_k={topk})`."
        )
    elif scope == "data_sources":
        folder_ids = list(opts.qa_data_sources.values_list("id", flat=True))
        folder_names = list(opts.qa_data_sources.values_list("name", flat=True))
        if folder_ids:
            search_lines.append(
                "Search the selected folder"
                + ("s" if len(folder_ids) != 1 else "")
                + " **"
                + ", ".join(folder_names)
                + "** using "
                + f"`rag_search(data_source_ids={folder_ids}, query=..., top_k={topk})`."
            )
        elif lib_id:
            search_lines.append(
                f"Search within selected folders of **{lib_name}** library."
            )
    elif scope == "documents":
        doc_ids = list(opts.qa_documents.values_list("id", flat=True))
        doc_names = list(opts.qa_documents.values_list("filename", flat=True))
        if doc_ids:
            search_lines.append(
                "Search the selected document"
                + ("s" if len(doc_ids) != 1 else "")
                + " **"
                + ", ".join(doc_names)
                + "** using "
                + f"`rag_search(document_ids={doc_ids}, query=..., top_k={topk})`."
            )
        elif lib_id:
            search_lines.append(
                f"Search within selected documents of **{lib_name}** library."
            )

    # RAG vs full-document mode
    qa_mode = opts.qa_mode or "rag"
    if qa_mode == "summarize":
        search_lines.append(
            "\nThis skill uses **full-document mode**: read entire documents "
            "using `get_document_text(document_id=...)` rather than searching "
            "for excerpts. For documents over 200k characters, make multiple "
            "calls advancing `start_char`."
        )
    else:
        search_lines.append(
            "\nSearch for relevant excerpts, then read them in full context "
            "using `get_document_text` if needed."
        )

    # Process mode
    process_mode = getattr(opts, "qa_process_mode", "combined_docs")
    if process_mode == "per_doc":
        search_lines.append(
            "Process each document separately. For batch processing, use "
            "`prompt_documents(document_ids=[...], prompt='...')`."
        )

    # Source order
    source_order = getattr(opts, "qa_source_order", "score")
    if source_order == "reading_order":
        search_lines.append(
            "Present findings in document reading order (by position) "
            "rather than by relevance score."
        )

    # History
    if getattr(opts, "qa_history", True):
        search_lines.append(
            "Consider conversation context when interpreting follow-up questions."
        )
    else:
        search_lines.append(
            "Treat each question independently from conversation history."
        )

    verbosity = getattr(opts, "qa_verbosity", "")
    if verbosity == "low":
        search_lines.append("Keep responses concise.")
    elif verbosity == "high":
        search_lines.append("Provide thorough, detailed responses.")

    parts.append("\n".join(search_lines))
    return "\n\n".join(parts)


def _build_qa_context_hints(opts):
    """Build context hints for a Q&A-mode preset."""
    hints = [{"type": "tool", "id": "local_qa_libraries", "name": "Q&A Libraries"}]

    lib = opts.qa_library
    if lib:
        hints.append({"type": "library", "id": str(lib.id), "name": lib.name})

    scope = opts.qa_scope or "all"
    if scope == "data_sources":
        for ds in opts.qa_data_sources.select_related("library").all():
            hints.append({"type": "folder", "id": str(ds.id), "name": str(ds)})
    elif scope == "documents":
        for doc in opts.qa_documents.select_related("data_source").all():
            hints.append({"type": "document", "id": str(doc.id), "name": doc.filename})

    return hints


def _build_summarize_body(opts):
    """Build skill body for a summarize-mode preset."""
    parts = []
    if opts.summarize_prompt:
        parts.append(opts.summarize_prompt.strip())

    parts.append(
        "\n## Processing\n\n"
        "To process documents, use "
        "`prompt_documents(document_ids=[...], prompt='...')`."
    )

    verbosity = getattr(opts, "summarize_verbosity", "")
    if verbosity == "low":
        parts.append("Keep summaries concise (2-3 sentences per document).")
    elif verbosity == "high":
        parts.append("Provide thorough, multi-paragraph summaries.")

    return "\n\n".join(parts)


def _build_translate_body(opts):
    """Build skill body for a translate-mode preset."""
    parts = []
    if opts.translate_prompt:
        parts.append(opts.translate_prompt.strip())

    lang = getattr(opts, "translate_language", "")
    if lang == "en":
        parts.append("Target language: **English**.")
    elif lang == "fr":
        parts.append("Target language: **French**.")

    glossary = getattr(opts, "translate_glossary_filename", "")
    if glossary:
        parts.append(f"Translation glossary: **{glossary}**.")

    parts.append(
        "\nNote: The built-in **Translation** skill (`translate-workflow`) "
        "provides a comprehensive translation workflow with TERMIUM Plus® "
        "and glossary support. Consider enabling it for full translation capabilities."
    )

    return "\n\n".join(parts)


async def _create_skill_from_preset(arguments: dict, context: ToolContext) -> dict:
    """Deterministic preset-to-skill migration.

    Copies the user's prompts verbatim and builds context hints from the
    preset configuration.  No LLM interpretation.
    """
    user = context.user
    preset_id = arguments["preset_id"]

    # Optional display name overrides
    name_en_override = arguments.get("display_name_en", "")
    name_fr_override = arguments.get("display_name_fr", "")

    def _do_migrate():
        from chat.models import Preset

        try:
            preset = Preset.objects.select_related(
                "options", "options__qa_library", "owner"
            ).get(id=preset_id, is_deleted=False)
        except Preset.DoesNotExist:
            return None, "Preset not found."

        # Verify migration visibility
        accessible = _get_migratable_presets_queryset(user).filter(id=preset_id)
        if not accessible.exists():
            return None, "You don't have access to this preset."

        opts = preset.options
        mode = opts.mode

        # --- Build body and context hints per mode ---
        context_hints = []
        if mode == "chat":
            body = _build_chat_body(opts)
        elif mode == "qa":
            body = _build_qa_body(opts)
            context_hints = _build_qa_context_hints(opts)
        elif mode == "summarize":
            body = _build_summarize_body(opts)
            context_hints.append(
                {
                    "type": "tool",
                    "id": "local_document_processing",
                    "name": "Batch Processing",
                }
            )
        elif mode == "translate":
            body = _build_translate_body(opts)
        else:
            body = opts.prompt or ""

        if not body:
            return None, (
                f"Preset '{preset.name_en or preset.name_fr}' has no "
                "prompt content to migrate."
            )

        # --- Determine display names / descriptions ---
        display_name_en = name_en_override or preset.name_en or ""
        display_name_fr = name_fr_override or preset.name_fr or ""
        description_en = preset.description_en or ""
        description_fr = preset.description_fr or ""

        # If no description, generate a minimal trigger based on mode
        if not description_en and display_name_en:
            description_en = f"Use this skill for {display_name_en}."
        if not description_fr and display_name_fr:
            description_fr = f"Utiliser cette compétence pour {display_name_fr}."

        # --- Create the skill ---
        skill = Skill.objects.create(
            display_name_en=display_name_en,
            display_name_fr=display_name_fr,
            description_en=description_en,
            description_fr=description_fr,
            body_en=body,
            body_fr="",  # Single-language transfer
            context_hints=context_hints,
            sharing_option="private",
            owner=user,
        )

        # Auto-enable for user
        from chat_next.models import ChatSettings

        settings_obj, _ = ChatSettings.objects.get_or_create_for_user(user)
        settings_obj.enabled_skills.add(skill)

        return {
            **_build_skill_link_result(skill),
            "preset_name": preset.name_en or preset.name_fr,
            "preset_mode": mode,
            "sharing_option": skill.sharing_option,
            "note": (
                "Skill created from preset with original prompts preserved. "
                "The skill is enabled and ready to use. "
                "To use it, select it from the context picker (the @ button "
                "in the chat input) and it will guide the AI's response. "
                "To edit the skill, open the Skills browser (lightbulb icon "
                "in the sidebar) or use the edit link."
            ),
        }, None

    result, error = await sync_to_async(_do_migrate)()

    if error:
        return {"success": False, "error": error}

    return {"success": True, "result": result}


TOOL_REGISTRY.register(
    OttoTool(
        name="create_skill_from_preset",
        description=(
            "Create a new Skill by deterministically migrating a legacy preset."
            # "Copies the preset's prompts verbatim and automatically configures "
            # "context hints based on the preset's mode and settings. "
            # "Use this for one-step preset-to-skill migration."
        ),
        parameters={
            "type": "object",
            "properties": {
                "preset_id": {
                    "type": "integer",
                    "description": "The ID of the preset to migrate.",
                },
                "display_name_en": {
                    "type": "string",
                    "description": (
                        "Optional English display name override."
                        # "Defaults to the preset's English name."
                    ),
                },
                "display_name_fr": {
                    "type": "string",
                    "description": (
                        "Optional French display name override."
                        # "Defaults to the preset's French name."
                    ),
                },
            },
            "required": ["preset_id"],
            "additionalProperties": False,
        },
        execute=_create_skill_from_preset,
        category=TOOL_CATEGORY_SKILLS,
        requires_user=True,
        requires_chat=False,
        permission_check=_is_authenticated_user,
        strict=False,
    )
)
