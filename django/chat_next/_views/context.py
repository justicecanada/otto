"""
Context autocomplete API for chat_next.

Provides a JSON endpoint that returns available tools, libraries,
folders, and documents for the context picker UI.
"""

from django.db import models as django_models
from django.http import JsonResponse

from rules.contrib.views import objectgetter
from structlog import get_logger

from otto.utils.decorators import permission_required

from chat_next.models import (
    AVAILABLE_TOOL_IDS,
    TOOL_CATEGORY_SKILLS,
    TOOL_DISPLAY,
    Chat,
)

logger = get_logger(__name__)

# How many items to show per category when there is no search query
DEFAULT_RECENT_LIMIT = 5


def _skill_picker_qs(chat, q: str):
    """Return enabled, accessible skills for the context picker.

    The chat context picker should only surface skills that are currently
    enabled in the user's chat settings and still accessible to them.
    """
    qs = (
        chat.settings.get_accessible_enabled_skills(chat.user)
        .only(
            "id",
            "owner",
            "display_name",
            "description",
            "display_name_en",
            "display_name_fr",
            "description_en",
            "description_fr",
        )
        .order_by("-updated_at")
    )

    if q:
        qs = qs.filter(
            django_models.Q(display_name_en__icontains=q)
            | django_models.Q(display_name_fr__icontains=q)
            | django_models.Q(description_en__icontains=q)
            | django_models.Q(description_fr__icontains=q)
        )

    return qs


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def context_autocomplete(request, chat_id):
    """Return JSON of available context items for the picker.

    Groups:
    - tools: high-level tool groups (Q&A libraries, Legislation, Web search, etc.)
    - libraries: Q&A libraries accessible to the user
    - folders: data-source folders within libraries
    - documents: documents from all accessible libraries (searchable by name)

    Query param ``q`` filters results by name (case-insensitive substring).
    When ``q`` is empty, returns the most recent items per category.
    """
    chat = Chat.objects.get(id=chat_id)
    q = request.GET.get("q", "").strip().lower()

    # Determine which tools the user currently has enabled
    from chat_next.models import sanitize_enabled_tools

    user_enabled_tools = set(sanitize_enabled_tools(chat.settings.chat_enabled_tools))

    items = []
    # Always show ALL available tools in the context picker so users can add
    # a tool as context even if it's disabled in their chat settings.
    tool_ids_to_show = list(AVAILABLE_TOOL_IDS)
    if request.user.is_admin and TOOL_CATEGORY_SKILLS not in tool_ids_to_show:
        tool_ids_to_show.append(TOOL_CATEGORY_SKILLS)

    # --- Skills (only skills enabled in the user's chat settings) ---
    skill_qs = _skill_picker_qs(chat, q)
    for skill in skill_qs[:DEFAULT_RECENT_LIMIT]:
        items.append(
            {
                "type": "skill",
                "id": skill.id,
                "name": str(skill.display_name),
                "description": str(skill.description) if skill.description else "",
                "icon": "lightbulb",
            }
        )

    # --- Tool groups (not individual tools) ---
    # Show each enabled tool category as a single selectable item
    tool_groups = _get_tool_groups(tool_ids_to_show, q, user_enabled_tools)
    items.extend(tool_groups)

    # --- Libraries, folders, documents ---
    from librarian.models import Document, Library

    accessible_libraries = (
        Library.objects.filter(
            django_models.Q(is_public=True)
            | django_models.Q(created_by=request.user)
            | django_models.Q(user_roles__user=request.user)
            | django_models.Q(team_roles__team__memberships__user=request.user)
        )
        .distinct()
        .order_by("-is_personal_library", "-is_public", "order", "-created_at")
    )

    # Collect ALL accessible library IDs for folder/document queries
    all_lib_ids = list(accessible_libraries.values_list("id", flat=True))

    lib_limit = 30 if q else DEFAULT_RECENT_LIMIT

    for lib in accessible_libraries[:lib_limit]:
        doc_count = (
            lib.data_sources.aggregate(
                total=django_models.Count(
                    "documents",
                    filter=django_models.Q(documents__is_container=False),
                )
            )["total"]
            or 0
        )
        if doc_count == 0 and not lib.is_personal_library:
            continue
        name = str(lib)
        desc = lib.description or ""
        if q and q not in name.lower() and q not in desc.lower():
            continue
        items.append(
            {
                "type": "library",
                "id": lib.id,
                "name": name,
                "description": f"{doc_count} documents"
                + (f" — {desc}" if desc else ""),
                "is_public": bool(lib.is_public),
            }
        )

    # --- Folders (DataSources) within accessible libraries ---
    from librarian.models import DataSource

    folder_qs = (
        DataSource.objects.filter(library_id__in=all_lib_ids)
        .exclude(chat__isnull=False)
        .exclude(chat_next__isnull=False)
        .annotate(
            doc_count=django_models.Count(
                "documents",
                filter=django_models.Q(documents__is_container=False),
            )
        )
        .filter(doc_count__gt=0)
        .select_related("library")
        .order_by("-modified_at")
    )
    if q:
        folder_qs = folder_qs.filter(name__icontains=q)

    folder_limit = 30 if q else DEFAULT_RECENT_LIMIT
    for ds in folder_qs[:folder_limit]:
        items.append(
            {
                "type": "folder",
                "id": ds.id,
                "name": str(ds),
                "description": f"in {ds.library} — {ds.doc_count} documents",
                "parent_library_id": ds.library_id,
                "parent_library_public": bool(ds.library and ds.library.is_public),
            }
        )

    # --- Documents (across all accessible libraries) ---
    doc_qs = (
        Document.objects.filter(
            data_source__library_id__in=all_lib_ids,
            is_container=False,
        )
        .select_related("data_source", "data_source__library")
        .order_by("-created_at")
    )
    if q:
        # Search by filename, extracted_title, manual_title, generated_title
        doc_qs = doc_qs.filter(
            django_models.Q(filename__icontains=q)
            | django_models.Q(extracted_title__icontains=q)
            | django_models.Q(manual_title__icontains=q)
            | django_models.Q(generated_title__icontains=q)
        )

    doc_limit = 30 if q else DEFAULT_RECENT_LIMIT
    for doc in doc_qs[:doc_limit]:
        doc_name = doc.name  # uses the @property
        lib_name = (
            str(doc.data_source.library)
            if doc.data_source and doc.data_source.library
            else ""
        )
        folder_name = str(doc.data_source) if doc.data_source else ""
        desc_parts = []
        if folder_name:
            desc_parts.append(folder_name)
        if lib_name and lib_name != folder_name:
            desc_parts.append(lib_name)
        items.append(
            {
                "type": "document",
                "id": doc.id,
                "name": doc_name,
                "description": " — ".join(desc_parts) if desc_parts else "",
            }
        )

    return JsonResponse({"items": items})


def _get_tool_groups(
    enabled_tools: list[str], q: str, user_enabled_tools: set[str]
) -> list[dict]:
    """Build high-level tool group items from enabled tool categories.

    Instead of listing every individual tool function, we group them into
    user-friendly categories that match AVAILABLE_TOOLS.
    """
    groups = []
    for tool_id in enabled_tools:
        info = TOOL_DISPLAY.get(tool_id)
        if not info:
            continue
        name = str(info["name"])
        description = str(info["description"])
        if q and q not in name.lower() and q not in description.lower():
            continue
        groups.append(
            {
                "type": "tool",
                "id": tool_id,
                "name": name,
                "description": description,
                "icon": info.get("icon", "tools"),
                "enabled": tool_id in user_enabled_tools,
            }
        )
    return groups
