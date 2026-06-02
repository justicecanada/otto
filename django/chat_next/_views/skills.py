"""Skills browser and CRUD views for chat_next."""

import json
import math
import re
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from rules.contrib.views import objectgetter
from structlog import get_logger

from otto.models import TeamMembership
from otto.rules import can_edit_skill
from otto.utils.decorators import budget_required, permission_required

from chat.utils import create_skill_shared_notification

from chat_next._utils.context_hints import check_context_hints
from chat_next._utils.skill_copy import clone_skill_for_user
from chat_next._utils.skill_import import (
    IN_PROGRESS_STATUSES as IMPORT_IN_PROGRESS_STATUSES,
)
from chat_next._utils.skill_import import (
    ParsedSkillImport,
    SkillImportError,
    parse_uploaded_skill_bytes,
)
from chat_next.forms import SkillForm, SkillImportForm, UploadForm
from chat_next.models import Chat, ChatSettings, Skill, SkillTag

logger = get_logger(__name__)

SKILL_BROWSER_QUERY_KEYS = (
    "q",
    "sharing",
    "tag",
    "featured",
    "enabled",
    "sort",
    "show_all_tags",
)


# Keep auto-suggestions focused on high-confidence matches.
MAX_EMBEDDING_DISTANCE = (
    0.75  # cosine distance = 1 - similarity (lowered to suggest more tags)
)
MIN_KEYWORD_SCORE = 1  # Lowered to suggest more tags
SKILL_IMPORT_TAG_PREFIX = "__otto_import__:"
SKILL_IMPORT_TAG_SOURCE_MARKDOWN = f"{SKILL_IMPORT_TAG_PREFIX}source:markdown"
SKILL_IMPORT_TAG_SOURCE_ZIP = f"{SKILL_IMPORT_TAG_PREFIX}source:zip"
SKILL_IMPORT_TAG_HAS_SCRIPTS = f"{SKILL_IMPORT_TAG_PREFIX}has_scripts"
SKILL_IMPORT_TAG_HAS_MCP = f"{SKILL_IMPORT_TAG_PREFIX}has_mcp"
SKILL_IMPORT_TAG_HAS_CLAUDE_MD = f"{SKILL_IMPORT_TAG_PREFIX}has_claude_md"


def _filled_skill_value(value) -> bool:
    return bool((value or "").strip())


def _skill_content_is_complete(skill) -> bool:
    return all(
        [
            _filled_skill_value(getattr(skill, "display_name_en", "")),
            _filled_skill_value(getattr(skill, "description_en", "")),
            _filled_skill_value(getattr(skill, "body_en", "")),
        ]
    ) or all(
        [
            _filled_skill_value(getattr(skill, "display_name_fr", "")),
            _filled_skill_value(getattr(skill, "description_fr", "")),
            _filled_skill_value(getattr(skill, "body_fr", "")),
        ]
    )


def _is_draft_skill(skill) -> bool:
    return skill is not None and not _skill_content_is_complete(skill)


def _normalize_sort_option(sort_option: str, search_query: str) -> str:
    """Normalize selected sort option with sensible defaults."""
    allowed = {"popular", "newest", "relevant"}
    if sort_option in allowed:
        return sort_option
    return "relevant" if search_query else "popular"


def _count_distinct_skill_ids(queryset) -> int:
    """Count skills by distinct IDs only, avoiding wide DISTINCT subqueries."""
    return queryset.order_by().values("id").distinct().count()


def _build_url(base_url, params=None):
    filtered = {}
    if params:
        for key in SKILL_BROWSER_QUERY_KEYS:
            value = params.get(key, "")
            if value not in (None, ""):
                filtered[key] = value
    if not filtered:
        return base_url
    return f"{base_url}?{urlencode(filtered, doseq=True)}"


def _append_query_params(url, params=None):
    filtered = {
        key: value for key, value in (params or {}).items() if value not in (None, "")
    }
    if not filtered:
        return url
    return f"{url}?{urlencode(filtered, doseq=True)}"


def _get_skill_browser_url(chat_id, params=None):
    return _build_url(reverse("chat_next:get_skills", args=[chat_id]), params)


def _get_skill_display_title(skill) -> str:
    return skill.display_name or _("Untitled skill")


def _build_skill_import_tags(parsed_import: ParsedSkillImport) -> list[str]:
    tags = [
        (
            SKILL_IMPORT_TAG_SOURCE_ZIP
            if parsed_import.source_type == "zip"
            else SKILL_IMPORT_TAG_SOURCE_MARKDOWN
        )
    ]

    if parsed_import.has_scripts:
        tags.append(SKILL_IMPORT_TAG_HAS_SCRIPTS)
    if parsed_import.has_mcp:
        tags.append(SKILL_IMPORT_TAG_HAS_MCP)
    if parsed_import.has_claude_md:
        tags.append(SKILL_IMPORT_TAG_HAS_CLAUDE_MD)

    return tags


def _get_persisted_skill_import_state(skill) -> dict | None:
    import_tags = {
        str(tag)
        for tag in (getattr(skill, "tags", None) or [])
        if str(tag).startswith(SKILL_IMPORT_TAG_PREFIX)
    }
    if not import_tags:
        return None

    source_type = "zip" if SKILL_IMPORT_TAG_SOURCE_ZIP in import_tags else "markdown"
    return {
        "source_type": source_type,
        "has_scripts": SKILL_IMPORT_TAG_HAS_SCRIPTS in import_tags,
        "has_mcp": SKILL_IMPORT_TAG_HAS_MCP in import_tags,
        "has_claude_md": SKILL_IMPORT_TAG_HAS_CLAUDE_MD in import_tags,
    }


def _get_imported_skill_display_name(parsed_import: ParsedSkillImport) -> str:
    return parsed_import.display_name or _("Imported skill")


def _get_skill_editor_url(request, chat_id, skill):
    return _append_query_params(
        reverse("chat_next:edit_skill", args=[chat_id, skill.id]),
        {"modal_back_url": _get_modal_back_url(request, chat_id)},
    )


def _get_skill_creator():
    """Return the featured helper skill used for skill creation shortcuts."""
    return Skill.objects.filter(
        owner__isnull=True,
        sharing_option="everyone",
        display_name_en="Skill Creator",
    ).first()


def _build_import_refinement_prompt(
    *,
    source_type: str,
    has_scripts: bool = False,
    has_mcp: bool = False,
    has_claude_md: bool = False,
) -> str:
    source_label = (
        _("Claude/Codex SKILL.md bundle")
        if source_type == "zip"
        else _("Claude/Codex SKILL.md file")
    )

    instructions = [
        _(
            "This skill was imported from a {source_label} and may require adaptation for Otto."
        ).format(source_label=source_label),
        _(
            "Review the skill description, body, and context hints and make any changes needed for Otto."
        ),
        _(
            "If the imported skill references supporting files in the skill folder, adapt the instructions so Otto can use them through context hints and skill files rather than local bundle-path assumptions."
        ),
        _(
            "If certain functionality cannot be implemented in Otto, explain that clearly to the user."
        ),
        _("Ask the user for input if any adaptation decision requires a choice."),
    ]

    if has_scripts:
        instructions.append(
            _(
                "Bundled scripts may exist only as supporting context files; Otto will not execute them automatically. If any part can reasonably be adapted to the OpenAI Code Interpreter tool, explain or implement that adaptation carefully."
            )
        )

    if has_mcp:
        instructions.append(
            _(
                "MCP connectors and imported connector configuration are not supported in Otto, so remove or rewrite those parts of the skill."
            )
        )

    if has_claude_md:
        instructions.append(
            _(
                "Any CLAUDE.md guidance was imported only as supporting context, not as automatically applied runtime behavior."
            )
        )

    instructions.append(
        _(
            "Do not assume arbitrary shell execution or direct MCP access. Work within Otto's existing tool and context-hint model."
        )
    )

    return "\n\n".join(instructions)


def _get_modal_back_url(request, chat_id):
    explicit_back_url = request.GET.get("modal_back_url") or request.POST.get(
        "modal_back_url"
    )
    if explicit_back_url:
        return explicit_back_url
    return _get_skill_browser_url(chat_id, request.GET or request.POST)


def _get_latest_bundle_import_document(skill):
    try:
        data_source = skill.data_source
    except Exception:
        return None

    return (
        data_source.documents.filter(
            parent_document__isnull=True,
            filename__iendswith=".zip",
        )
        .order_by("-created_at", "-id")
        .first()
    )


def _get_bundle_import_status(skill):
    bundle_document = _get_latest_bundle_import_document(skill)
    if not bundle_document:
        return None

    child_documents = bundle_document.child_documents.all()
    child_total = child_documents.count()
    child_success = child_documents.filter(status="SUCCESS").count()
    child_error = child_documents.filter(status="ERROR").count()
    child_processing = child_documents.filter(
        status__in=IMPORT_IN_PROGRESS_STATUSES
    ).count()

    if bundle_document.status == "ERROR":
        return {
            "state": "error",
            "status_text": bundle_document.status_details
            or _("There was an error importing the bundle's supporting files."),
            "child_total": child_total,
            "child_success": child_success,
            "child_error": child_error,
            "child_processing": child_processing,
        }

    if bundle_document.status in IMPORT_IN_PROGRESS_STATUSES:
        return {
            "state": "processing",
            "status_text": bundle_document.celery_status_message
            or _("Importing supporting files..."),
            "child_total": child_total,
            "child_success": child_success,
            "child_error": child_error,
            "child_processing": child_processing,
        }

    if child_processing > 0:
        return {
            "state": "processing",
            "status_text": _(
                "Processing extracted supporting files for the imported bundle..."
            ),
            "child_total": child_total,
            "child_success": child_success,
            "child_error": child_error,
            "child_processing": child_processing,
        }

    return {
        "state": "complete",
        "status_text": _(
            "Supporting files from the imported bundle are available in Skill files."
        ),
        "child_total": child_total,
        "child_success": child_success,
        "child_error": child_error,
        "child_processing": child_processing,
    }


def _render_skill_card_list(request, chat_id, params):
    """Render skills card list using current filter/search/sort params."""
    user_settings = _get_user_settings(request.user)
    enabled_skill_ids = list(
        user_settings.get_accessible_enabled_skills(request.user).values_list(
            "id", flat=True
        )
    )
    active_skill_ids = set(enabled_skill_ids)
    accessible_skill_ids = list(
        Skill.objects.get_accessible(request.user)
        .order_by()
        .values_list("id", flat=True)
    )
    accessible_skills = Skill.objects.filter(
        id__in=accessible_skill_ids
    ).select_related("owner")
    skills = accessible_skills.prefetch_related("skill_tags")
    total_visible_count = len(accessible_skill_ids)

    # Get filter params
    tag_filter = params.get("tag", "")
    sharing_filter = params.get("sharing", "")
    featured_filter = params.get("featured", "")
    enabled_filter = params.get("enabled", "")
    search_query_raw = params.get("q", "") or ""
    search_query = search_query_raw.strip()
    show_all_tags = params.get("show_all_tags", "")
    sort_option = _normalize_sort_option(params.get("sort", ""), search_query)

    if tag_filter:
        skills = skills.filter(skill_tags__name=tag_filter)
    if featured_filter == "1":
        # Only featured — but may combine with sharing filters below
        pass  # handled in combo logic below
    if sharing_filter:
        filters = [f.strip() for f in sharing_filter.split(",") if f.strip()]
        q = Q()
        requires_distinct = False
        for f in filters:
            if f == "mine":
                q |= Q(owner=request.user)
            elif f == "everyone":
                q |= Q(sharing_option="everyone")
            elif f == "shared_with_me":
                requires_distinct = True
                q |= (
                    Q(accessible_to=request.user)
                    | Q(editable_by=request.user)
                    | Q(accessible_to_teams__memberships__user=request.user)
                    | Q(editable_by_teams__memberships__user=request.user)
                ) & ~Q(owner=request.user)
        if featured_filter == "1":
            q |= Q(is_featured=True)
        skills = skills.filter(q)
        if requires_distinct:
            skills = skills.distinct()
    elif featured_filter == "1":
        skills = skills.filter(is_featured=True)

    if enabled_filter == "1":
        skills = skills.filter(id__in=enabled_skill_ids)

    if search_query:
        skills = skills.filter(
            Q(display_name_en__icontains=search_query)
            | Q(display_name_fr__icontains=search_query)
            | Q(description_en__icontains=search_query)
            | Q(description_fr__icontains=search_query)
        )
    has_filters = bool(
        tag_filter
        or sharing_filter
        or featured_filter == "1"
        or enabled_filter == "1"
        or search_query
    )
    filtered_count = (
        _count_distinct_skill_ids(skills) if has_filters else total_visible_count
    )

    if sort_option == "newest":
        skills = skills.order_by("-created_at", "-id")
    elif sort_option == "popular":
        skills = skills.order_by("-load_count", "-updated_at", "-id")
    elif search_query:
        skills = (
            skills.annotate(
                relevance_score=Case(
                    When(display_name_en__icontains=search_query, then=Value(3)),
                    When(display_name_fr__icontains=search_query, then=Value(3)),
                    When(description_en__icontains=search_query, then=Value(2)),
                    When(description_fr__icontains=search_query, then=Value(2)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            )
            .order_by("-relevance_score", "-load_count", "-updated_at", "-id")
            .distinct()
        )
    else:
        skills = skills.order_by("-load_count", "-updated_at", "-id")

    # Collect tags sorted by popularity (number of skills using each tag)
    MAX_SIDEBAR_TAGS = 10
    all_tags_qs = (
        SkillTag.objects.filter(skills__id__in=accessible_skill_ids)
        .annotate(
            usage_count=Count(
                "skills",
                filter=Q(skills__id__in=accessible_skill_ids),
                distinct=True,
            )
        )
        .order_by("-usage_count", "id")
        .distinct()
    )
    if show_all_tags:
        all_tags = list(all_tags_qs)
        has_more_tags = False
    else:
        sidebar_tags = list(all_tags_qs[: MAX_SIDEBAR_TAGS + 1])
        has_more_tags = len(sidebar_tags) > MAX_SIDEBAR_TAGS
        all_tags = sidebar_tags[:MAX_SIDEBAR_TAGS]

    # Look up the "Skill creator" skill so we can offer a help shortcut.
    skill_creator = _get_skill_creator()

    return render(
        request,
        "chat_next/modals/shared/skills_browser_content.html",
        {
            "skills": skills,
            "chat_id": chat_id,
            "active_skill_ids": active_skill_ids,
            "all_tags": all_tags,
            "has_more_tags": has_more_tags,
            "tag_filter": tag_filter,
            "sharing_filter": sharing_filter,
            "featured_filter": featured_filter,
            "enabled_filter": enabled_filter,
            "search_query": search_query_raw,
            "sort_option": sort_option,
            "filtered_count": filtered_count,
            "total_visible_count": total_visible_count,
            "show_all_tags": show_all_tags,
            "skill_creator": skill_creator,
            "current_url": _get_skill_browser_url(chat_id, params),
        },
    )


def _ensure_skill_folder_hint(skill, data_source):
    hints = list(skill.context_hints or [])
    folder_name = _get_skill_display_title(skill)
    folder_hint = {
        "type": "folder",
        "id": str(data_source.id),
        "name": folder_name,
    }
    existing_folder_ids = {str(h.get("id")) for h in hints if h.get("type") == "folder"}
    if str(data_source.id) not in existing_folder_ids:
        hints.append(folder_hint)
        skill.context_hints = hints
        skill.save(update_fields=["context_hints"])
    else:
        _sync_skill_folder_metadata(skill)


def _queue_import_bundle_document(*, user, skill, filename, content, content_type=""):
    from librarian.models import Document
    from librarian.utils.process_document import save_content_to_saved_file

    data_source = _get_or_create_skill_data_source(user, skill)
    folder_name = _get_skill_display_title(skill)
    if data_source.name != folder_name:
        data_source.name = folder_name
        data_source.save(update_fields=["name"])

    _ensure_skill_folder_hint(skill, data_source)

    saved_file, resolved_name, _sanitized_content_type = save_content_to_saved_file(
        content,
        filename=filename,
        content_type=content_type,
    )
    bundle_document = Document.objects.create(
        data_source=data_source,
        saved_file=saved_file,
        filename=resolved_name,
        provenance=Document.PROVENANCE_USER_UPLOAD,
    )
    bundle_document.process()
    return bundle_document


def _can_admin_edit_skill(user, skill) -> bool:
    """Otto admins may edit default/shared skills visible to all users."""
    return bool(user.has_perm("chat_next.admin_edit_skill", skill))


def _can_delete_skill(user, skill) -> bool:
    """Owner can delete; admins may delete skills they can admin-edit."""
    return bool(skill.owner == user or _can_admin_edit_skill(user, skill))


def _get_user_settings(user):
    settings, _ = ChatSettings.objects.get_or_create_for_user(user)
    return settings


def _get_tag_display_language(request) -> str:
    """Return the UI language to use for tag labels."""
    language = (getattr(request, "LANGUAGE_CODE", "en") or "en").lower()
    return "fr" if language.startswith("fr") else "en"


def _get_skill_tags_json(skill, lang):
    """Return JSON array of tag objects for the skill, for template use."""
    tags = []
    for tag in skill.skill_tags.all():
        tags.append(
            {
                "id": tag.id,
                "name_en": tag.name_en or "",
                "name_fr": tag.name_fr or "",
                "display": (
                    tag.name_en if lang == "en" else (tag.name_fr or tag.name_en)
                )
                or "",
            }
        )
    return json.dumps(tags)


def _save_skill_tags(request, skill):
    """Process tag data from form POST and update the skill's M2M tags."""
    tags_raw = request.POST.get("skill_tags", "")
    tags_en_raw = request.POST.get("skill_tags_en", "[]")
    tags_fr_raw = request.POST.get("skill_tags_fr", "[]")
    tags_source_lang = request.POST.get("skill_tags_source_lang", "").strip().lower()

    try:
        tags = json.loads(tags_raw) if tags_raw else []
    except (json.JSONDecodeError, TypeError):
        tags = []
    try:
        tags_en = json.loads(tags_en_raw)
    except (json.JSONDecodeError, TypeError):
        tags_en = []
    try:
        tags_fr = json.loads(tags_fr_raw)
    except (json.JSONDecodeError, TypeError):
        tags_fr = []

    if tags:
        tags_payload = tags
    elif tags_source_lang == "en":
        tags_payload = tags_en
    elif tags_source_lang == "fr":
        tags_payload = tags_fr
    else:
        # Backward-compatible fallback when source language is not provided.
        tags_payload = tags_en + tags_fr

    # Merge tags from both language tabs by id, creating new ones as needed
    tag_objects = []
    seen_ids = set()

    for tag_data in tags_payload:
        tag_id = tag_data.get("id")

        # Skip duplicates
        if tag_id and tag_id in seen_ids:
            continue

        if tag_id:
            # Existing tag
            try:
                tag_obj = SkillTag.objects.get(id=tag_id)
                seen_ids.add(tag_id)
                tag_objects.append(tag_obj)
            except SkillTag.DoesNotExist:
                pass
        elif tag_data.get("is_new"):
            # Create new tag
            name_en = tag_data.get("name_en", "").strip()
            name_fr = tag_data.get("name_fr", "").strip()
            if not name_en and not name_fr:
                continue
            # Use the display name as fallback
            display = tag_data.get("display", "").strip()
            if not name_en:
                name_en = display
            if not name_fr:
                name_fr = display
            # Check for existing tag with same name (case-insensitive)
            existing = SkillTag.objects.filter(
                Q(name_en__iexact=name_en) | Q(name_fr__iexact=name_fr)
            ).first()
            if existing:
                if existing.id not in seen_ids:
                    seen_ids.add(existing.id)
                    tag_objects.append(existing)
            else:
                tag_obj = SkillTag.objects.create(
                    name=name_en,
                    name_en=name_en,
                    name_fr=name_fr,
                )
                seen_ids.add(tag_obj.id)
                tag_objects.append(tag_obj)

    skill.skill_tags.set(tag_objects)


def _capture_skill_sharing_state(skill) -> dict:
    if not skill or not skill.pk:
        return {
            "direct_access_user_ids": set(),
            "direct_edit_user_ids": set(),
            "access_team_ids": set(),
            "edit_team_ids": set(),
        }

    return {
        "direct_access_user_ids": set(skill.accessible_to.values_list("id", flat=True)),
        "direct_edit_user_ids": set(skill.editable_by.values_list("id", flat=True)),
        "access_team_ids": set(skill.accessible_to_teams.values_list("id", flat=True)),
        "edit_team_ids": set(skill.editable_by_teams.values_list("id", flat=True)),
    }


def _get_team_member_names_by_user(team_ids: set[int]) -> dict[int, set[str]]:
    if not team_ids:
        return {}

    rows = TeamMembership.objects.filter(team_id__in=team_ids).values_list(
        "user_id", "team__name"
    )
    mapping: dict[int, set[str]] = {}
    for user_id, team_name in rows:
        mapping.setdefault(user_id, set()).add(team_name)
    return mapping


def _notify_new_skill_share_recipients(request, skill, previous_state: dict):
    if not skill or not skill.pk:
        return

    current_state = _capture_skill_sharing_state(skill)

    previous_access_team_names = _get_team_member_names_by_user(
        previous_state["access_team_ids"]
    )
    previous_edit_team_names = _get_team_member_names_by_user(
        previous_state["edit_team_ids"]
    )
    current_access_team_names = _get_team_member_names_by_user(
        current_state["access_team_ids"]
    )
    current_edit_team_names = _get_team_member_names_by_user(
        current_state["edit_team_ids"]
    )

    previous_effective_user_ids = (
        previous_state["direct_access_user_ids"]
        | previous_state["direct_edit_user_ids"]
        | set(previous_access_team_names)
        | set(previous_edit_team_names)
    )
    current_effective_user_ids = (
        current_state["direct_access_user_ids"]
        | current_state["direct_edit_user_ids"]
        | set(current_access_team_names)
        | set(current_edit_team_names)
    )

    previous_edit_user_ids = previous_state["direct_edit_user_ids"] | set(
        previous_edit_team_names
    )
    current_edit_user_ids = current_state["direct_edit_user_ids"] | set(
        current_edit_team_names
    )

    actor_id = getattr(request.user, "id", None)
    notify_user_ids = {
        user_id
        for user_id in current_effective_user_ids
        if user_id != actor_id
        and (
            user_id not in previous_effective_user_ids
            or (
                user_id in current_edit_user_ids
                and user_id not in previous_edit_user_ids
            )
        )
    }

    if not notify_user_ids:
        return

    new_direct_user_ids = (
        current_state["direct_access_user_ids"] | current_state["direct_edit_user_ids"]
    ) - (
        previous_state["direct_access_user_ids"]
        | previous_state["direct_edit_user_ids"]
    )

    User = get_user_model()
    recipients = {user.id: user for user in User.objects.filter(id__in=notify_user_ids)}

    for user_id in sorted(notify_user_ids):
        recipient = recipients.get(user_id)
        if not recipient:
            continue

        via_team_names = sorted(
            (
                current_access_team_names.get(user_id, set())
                | current_edit_team_names.get(user_id, set())
            )
            - (
                previous_access_team_names.get(user_id, set())
                | previous_edit_team_names.get(user_id, set())
            )
        )
        shared_via_team = bool(via_team_names) and user_id not in new_direct_user_ids
        team_name = (
            via_team_names[0] if shared_via_team and len(via_team_names) == 1 else None
        )

        create_skill_shared_notification(
            recipient,
            skill,
            request.user,
            can_edit=user_id in current_edit_user_ids,
            shared_via_team=shared_via_team,
            team_name=team_name,
        )


def _save_skill_form_instance(request, form, *, skill=None):
    """Persist a skill form and normalize context hints from the request."""
    previous_sharing_state = _capture_skill_sharing_state(skill)
    skill_obj = form.save(commit=False)
    if skill is None:
        skill_obj.owner = request.user

    context_hints_raw = request.POST.get("context_hints", "[]")
    try:
        skill_obj.context_hints = json.loads(context_hints_raw)
    except (json.JSONDecodeError, TypeError):
        skill_obj.context_hints = []

    if skill is None:
        skill_obj.load_count = 0

    skill_obj.save()
    form.save_m2m()
    form.save_sharing(skill_obj)
    _notify_new_skill_share_recipients(request, skill_obj, previous_sharing_state)
    _save_skill_tags(request, skill_obj)
    _sync_skill_folder_metadata(skill_obj)
    return skill_obj


def _sync_skill_folder_metadata(skill, *, previous_name=""):
    """Keep the skill folder name and folder context hint aligned with the skill."""
    folder_name = _get_skill_display_title(skill)
    updated_fields = []

    try:
        data_source = skill.data_source
    except Exception:
        data_source = None

    if data_source and data_source.name != folder_name:
        data_source.name = folder_name
        data_source.save(update_fields=["name"])

    hints = []
    hints_changed = False
    data_source_id = str(data_source.id) if data_source else None
    for hint in list(skill.context_hints or []):
        if (
            hint.get("type") == "folder"
            and data_source_id
            and str(hint.get("id")) == data_source_id
            and hint.get("name") != folder_name
        ):
            hint = {**hint, "name": folder_name}
            hints_changed = True
        hints.append(hint)

    if hints_changed:
        skill.context_hints = hints
        updated_fields.append("context_hints")

    if updated_fields:
        skill.save(update_fields=updated_fields)


def _attach_saved_files_to_skill(*, user, skill, saved_files):
    """Attach uploaded files to a skill folder and keep its folder hint present."""
    from librarian.models import Document

    data_source = _get_or_create_skill_data_source(user, skill)
    folder_name = _get_skill_display_title(skill)
    if data_source.name != folder_name:
        data_source.name = folder_name
        data_source.save(update_fields=["name"])

    new_documents = []
    for saved_file in saved_files:
        file_obj = saved_file["saved_file"]
        filename = saved_file["filename"]

        existing = Document.objects.filter(
            data_source=data_source,
            filename=filename,
            saved_file=file_obj,
        ).first()
        if existing:
            if existing.provenance == Document.PROVENANCE_UNKNOWN:
                existing.provenance = Document.PROVENANCE_USER_UPLOAD
                existing.save(update_fields=["provenance"])
            if existing.status in ["ERROR", "PENDING"]:
                existing.process()
            new_documents.append(existing)
            continue

        document = Document.objects.create(
            data_source_id=data_source.id,
            saved_file=file_obj,
            filename=filename,
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        document.process()
        new_documents.append(document)

    hints = list(skill.context_hints or [])
    folder_hint = {
        "type": "folder",
        "id": str(data_source.id),
        "name": data_source.name,
    }
    existing_folder_ids = {str(h.get("id")) for h in hints if h.get("type") == "folder"}
    if str(data_source.id) not in existing_folder_ids:
        hints.append(folder_hint)
        skill.context_hints = hints
        skill.save(update_fields=["context_hints"])
    else:
        _sync_skill_folder_metadata(skill)

    return data_source, new_documents


def _render_skill_form(
    request,
    chat_id,
    skill,
    user_settings,
    *,
    form=None,
    upload_form=None,
    import_notice_items=None,
    import_refine_prompt="",
):
    """Render the skill detail view with current enabled/editable state."""
    if form is None:
        form = SkillForm(instance=skill, user=request.user)
    if upload_form is None:
        upload_form = UploadForm(prefix="skill")

    tag_display_lang = _get_tag_display_language(request)
    can_admin_edit = _can_admin_edit_skill(request.user, skill)
    can_edit = can_edit_skill(request.user, skill)
    can_delete = _can_delete_skill(request.user, skill)
    can_change_sharing_option = bool(
        getattr(form, "can_change_sharing_option", skill.owner_id == request.user.id)
    )
    sharing_option_value = form["sharing_option"].value()
    if sharing_option_value in (None, ""):
        sharing_option_value = skill.sharing_option or "private"
    active_skills = user_settings.get_accessible_enabled_skills(request.user)
    is_draft = _is_draft_skill(skill)
    bundle_import_status = _get_bundle_import_status(skill)
    persisted_import_state = _get_persisted_skill_import_state(skill)

    if not import_refine_prompt and persisted_import_state:
        import_refine_prompt = _build_import_refinement_prompt(
            source_type=persisted_import_state["source_type"],
            has_scripts=persisted_import_state["has_scripts"],
            has_mcp=persisted_import_state["has_mcp"],
            has_claude_md=persisted_import_state["has_claude_md"],
        )

    # Get skill data source ID for manage files link
    skill_data_source_id = None
    try:
        skill_data_source_id = skill.data_source.id
    except Exception:
        pass

    return render(
        request,
        "chat_next/modals/shared/skill_form_content.html",
        {
            "form": form,
            "upload_form": upload_form,
            "chat_id": chat_id,
            "skill": skill,
            "skill_display_title": _get_skill_display_title(skill),
            "active_skills": active_skills,
            "can_edit": can_edit,
            "can_delete": can_delete,
            "can_copy": Skill.objects.get_accessible(request.user)
            .filter(id=skill.id)
            .exists()
            and not is_draft,
            "can_change_sharing_option": can_change_sharing_option,
            "can_admin_edit": can_admin_edit,
            "is_admin": bool(request.user.has_perm("chat_next.manage_featured_skills")),
            "is_public": skill.sharing_option == "everyone",
            "is_draft": is_draft,
            "is_skill_enabled": active_skills.filter(id=skill.id).exists(),
            "sharing_option_value": sharing_option_value,
            "show_sharing_controls": sharing_option_value in {"others", "everyone"},
            "show_share_recipients": sharing_option_value == "others",
            "show_editor_controls": sharing_option_value in {"others", "everyone"},
            "tag_display_lang": tag_display_lang,
            "skill_tags_json": _get_skill_tags_json(skill, tag_display_lang),
            "context_hints_json": json.dumps(skill.context_hints or []),
            "context_hint_statuses": check_context_hints(skill.context_hints or []),
            "skill_data_source_id": skill_data_source_id,
            "modal_back_url": _get_modal_back_url(request, chat_id),
            "current_url": _get_skill_editor_url(request, chat_id, skill),
            "skill_creator": _get_skill_creator(),
            "import_notice_items": import_notice_items or [],
            "import_refine_prompt": import_refine_prompt or "",
            "bundle_import_status": bundle_import_status,
            "bundle_import_status_url": (
                reverse("chat_next:skill_import_status", args=[chat_id, skill.id])
                if bundle_import_status
                else ""
            ),
        },
    )


def _render_new_skill_form(
    request,
    chat_id,
    user_settings,
    *,
    form=None,
    upload_form=None,
    import_form=None,
):
    if form is None:
        form = SkillForm(user=request.user)
    if upload_form is None:
        upload_form = UploadForm(prefix="skill")
    if import_form is None:
        import_form = SkillImportForm(prefix="skill_import")

    active_skills = user_settings.get_accessible_enabled_skills(request.user)
    sharing_option_value = form["sharing_option"].value() or "private"

    return render(
        request,
        "chat_next/modals/shared/skill_form_content.html",
        {
            "form": form,
            "upload_form": upload_form,
            "import_form": import_form,
            "chat_id": chat_id,
            "active_skills": active_skills,
            "can_edit": True,
            "can_copy": False,
            "can_change_sharing_option": True,
            "is_public": False,
            "is_new_skill": True,
            "sharing_option_value": sharing_option_value,
            "show_sharing_controls": sharing_option_value in {"others", "everyone"},
            "show_share_recipients": sharing_option_value == "others",
            "show_editor_controls": sharing_option_value in {"others", "everyone"},
            "tag_display_lang": _get_tag_display_language(request),
            "skill_tags_json": request.POST.get("skill_tags", "[]") or "[]",
            "context_hints_json": request.POST.get("context_hints", "[]") or "[]",
            "context_hint_statuses": {},
            "modal_back_url": _get_modal_back_url(request, chat_id),
            "current_url": request.get_full_path(),
            "skill_creator": _get_skill_creator(),
            "import_notice_items": [],
            "import_refine_prompt": "",
            "bundle_import_status": None,
            "bundle_import_status_url": "",
        },
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def get_skills(request, chat_id):
    """Skills browser modal body — list of skill cards with filters."""
    return _render_skill_card_list(request, chat_id, request.GET)


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
@require_POST
def add_skill(request, chat_id, skill_id):
    """Add a skill to user's enabled skills."""
    user_settings = _get_user_settings(request.user)
    skill = get_object_or_404(Skill, id=skill_id)

    # Verify user can access this skill
    accessible = Skill.objects.get_accessible(request.user).filter(id=skill_id)
    if not accessible.exists():
        messages.error(request, _("You don't have access to this skill."))
    elif _is_draft_skill(skill):
        messages.error(
            request,
            _(
                "Finish the skill's Display name, Description, and Prompt in one language before enabling it."
            ),
        )
    else:
        user_settings.enabled_skills.add(skill)

    return _render_skills_response(request, chat_id)


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
@require_POST
def remove_skill(request, chat_id, skill_id):
    """Remove a skill from user's enabled skills."""
    user_settings = _get_user_settings(request.user)
    skill = get_object_or_404(Skill, id=skill_id)
    user_settings.enabled_skills.remove(skill)

    return _render_skills_response(request, chat_id)


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
@require_POST
def toggle_skill(request, chat_id, skill_id):
    """Toggle a skill on/off in user's enabled skills."""
    user_settings = _get_user_settings(request.user)
    skill = get_object_or_404(Skill, id=skill_id)

    if user_settings.enabled_skills.filter(id=skill_id).exists():
        user_settings.enabled_skills.remove(skill)
    else:
        # Verify user can access this skill
        accessible = Skill.objects.get_accessible(request.user).filter(id=skill_id)
        if not accessible.exists():
            messages.error(request, _("You don't have access to this skill."))
        elif _is_draft_skill(skill):
            messages.error(
                request,
                _(
                    "Finish the skill's Display name, Description, and Prompt in one language before enabling it."
                ),
            )
        else:
            user_settings.enabled_skills.add(skill)

    if request.POST.get("return_to_skill") == "true":
        return _render_skill_form(request, chat_id, skill, user_settings)

    # Re-render the active skills panel, preserving state
    active_skills = user_settings.get_accessible_enabled_skills(request.user)
    current_skill_id = request.POST.get("current_skill_id", "")
    is_new_skill = request.POST.get("is_new_skill", "false") == "true"

    is_current_skill_enabled = False
    if current_skill_id:
        try:
            is_current_skill_enabled = active_skills.filter(
                id=int(current_skill_id)
            ).exists()
        except (ValueError, TypeError):
            pass

    return render(
        request,
        "chat_next/modals/skills/active_skills_panel.html",
        {
            "chat_id": chat_id,
            "active_skills": active_skills,
            "current_skill_id": current_skill_id,
            "is_new_skill": is_new_skill,
            "is_current_skill_enabled": is_current_skill_enabled,
        },
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def create_skill(request, chat_id):
    """Skill editor form — GET shows form, POST creates skill."""
    user_settings = _get_user_settings(request.user)
    is_upload_submission = (
        request.method == "POST" and request.POST.get("skill_upload_flow") == "1"
    )

    if request.method == "POST":
        form = SkillForm(
            request.POST,
            user=request.user,
            allow_incomplete=is_upload_submission,
        )
        upload_form = (
            UploadForm(request.POST, request.FILES, prefix="skill")
            if is_upload_submission
            else UploadForm(prefix="skill")
        )
        if form.is_valid():
            if is_upload_submission and not upload_form.is_valid():
                logger.error("Skill create+upload error.", errors=upload_form.errors)
                form.add_error(None, _("There was an error uploading your files."))
            else:
                saved_files = upload_form.save() if is_upload_submission else []
                if is_upload_submission and not saved_files:
                    form.add_error(None, _("No files were uploaded."))
                else:
                    skill = _save_skill_form_instance(request, form)
                    if saved_files:
                        _attach_saved_files_to_skill(
                            user=request.user,
                            skill=skill,
                            saved_files=saved_files,
                        )
                        messages.success(
                            request,
                            _(
                                "Draft skill created successfully and files were uploaded. Finish the skill details to enable it."
                            )
                            if not form.content_is_complete
                            else _(
                                "Skill created successfully and files were uploaded."
                            ),
                        )
                    else:
                        messages.success(
                            request,
                            _("Draft skill created successfully.")
                            if not form.content_is_complete
                            else _("Skill created successfully."),
                        )
                    if form.content_is_complete:
                        user_settings.enabled_skills.add(skill)
                    return _render_skill_form(request, chat_id, skill, user_settings)
    else:
        form = SkillForm(user=request.user)
        upload_form = UploadForm(prefix="skill")

    return _render_new_skill_form(
        request,
        chat_id,
        user_settings,
        form=form,
        upload_form=upload_form,
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
@require_POST
def import_skill(request, chat_id):
    user_settings = _get_user_settings(request.user)
    import_form = SkillImportForm(request.POST, request.FILES, prefix="skill_import")

    if not import_form.is_valid():
        return _render_new_skill_form(
            request,
            chat_id,
            user_settings,
            import_form=import_form,
        )

    uploaded_file = import_form.cleaned_data["skill_file"]
    file_content = uploaded_file.read()

    try:
        parsed_import = parse_uploaded_skill_bytes(
            content=file_content,
            filename=uploaded_file.name,
        )
    except SkillImportError as exc:
        import_form.add_error("skill_file", str(exc))
        return _render_new_skill_form(
            request,
            chat_id,
            user_settings,
            import_form=import_form,
        )

    skill = Skill.objects.create(
        display_name_en=_get_imported_skill_display_name(parsed_import),
        display_name_fr="",
        description_en=parsed_import.description,
        description_fr="",
        body_en=parsed_import.body,
        body_fr="",
        tags=_build_skill_import_tags(parsed_import),
        owner=request.user,
        sharing_option="private",
    )

    if parsed_import.source_type == "zip" and parsed_import.supporting_file_count > 0:
        _queue_import_bundle_document(
            user=request.user,
            skill=skill,
            filename=uploaded_file.name,
            content=file_content,
            content_type=getattr(uploaded_file, "content_type", "")
            or "application/zip",
        )

    import_notice_items = [
        _(
            "Imported skills stay private and disabled until you review and adapt them for Otto."
        )
    ]
    if parsed_import.source_type == "zip" and parsed_import.supporting_file_count > 0:
        import_notice_items.append(
            _(
                "Supporting files from the uploaded bundle are being imported into Skill files in the background."
            )
        )
    import_notice_items.extend(parsed_import.notes)

    messages.success(
        request,
        _(
            "Skill imported successfully. Review it and consider refining it with Skill Creator before enabling it."
        ),
    )

    return _render_skill_form(
        request,
        chat_id,
        skill,
        user_settings,
        import_notice_items=import_notice_items,
        import_refine_prompt=_build_import_refinement_prompt(
            source_type=parsed_import.source_type,
            has_scripts=parsed_import.has_scripts,
            has_mcp=parsed_import.has_mcp,
            has_claude_md=parsed_import.has_claude_md,
        ),
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def edit_skill(request, chat_id, skill_id):
    """Skill viewer/editor — read-only if user lacks edit permission."""
    user_settings = _get_user_settings(request.user)
    skill = get_object_or_404(
        Skill.objects.get_accessible(request.user),
        id=skill_id,
    )

    can_edit = can_edit_skill(request.user, skill)

    if request.method == "POST":
        if not can_edit:
            messages.error(request, _("You don't have permission to edit this skill."))
            return _render_skills_response(request, chat_id)
        form = SkillForm(request.POST, instance=skill, user=request.user)
        if form.is_valid():
            skill_obj = _save_skill_form_instance(request, form, skill=skill)
            messages.success(request, _("Skill updated successfully."))
            return _render_skill_form(request, chat_id, skill_obj, user_settings)
    else:
        form = SkillForm(instance=skill, user=request.user)

    return _render_skill_form(request, chat_id, skill, user_settings, form=form)


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def skill_import_status(request, chat_id, skill_id):
    skill = get_object_or_404(
        Skill.objects.get_accessible(request.user),
        id=skill_id,
    )
    status = _get_bundle_import_status(skill)
    if not status:
        return HttpResponse("")

    return render(
        request,
        "chat_next/modals/skills/import_status.html",
        {
            "bundle_import_status": status,
            "bundle_import_status_url": reverse(
                "chat_next:skill_import_status", args=[chat_id, skill.id]
            ),
        },
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
@require_POST
def delete_skill(request, chat_id, skill_id):
    """Delete a skill and its associated skill folder, if present."""
    from librarian.models import DataSource

    skill = get_object_or_404(Skill, id=skill_id)

    if not _can_delete_skill(request.user, skill):
        messages.error(request, _("You don't have permission to delete this skill."))
    else:
        try:
            skill.data_source.delete()
        except DataSource.DoesNotExist:
            pass
        skill.delete()
        messages.success(request, _("Skill deleted."))

    return _render_skills_response(request, chat_id)


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
@require_POST
def copy_skill(request, chat_id, skill_id):
    """Create a private, disabled copy of a skill for the requesting user."""
    user_settings = _get_user_settings(request.user)
    source_skill = get_object_or_404(
        Skill.objects.get_accessible(request.user),
        id=skill_id,
    )

    copied_skill = clone_skill_for_user(
        user=request.user,
        source_skill=source_skill,
    )
    messages.success(request, _("Private copy created successfully."))
    return _render_skill_form(request, chat_id, copied_skill, user_settings)


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
@require_POST
def toggle_featured(request, chat_id, skill_id):
    """Toggle the Featured flag on a skill (admin-only)."""
    if not request.user.has_perm("chat_next.manage_featured_skills"):
        messages.error(request, _("Only admins can feature skills."))
        return _render_skills_response(request, chat_id)

    skill = get_object_or_404(Skill, id=skill_id)
    skill.is_featured = not skill.is_featured
    skill.save(update_fields=["is_featured"])

    user_settings = _get_user_settings(request.user)
    return _render_skill_form(request, chat_id, skill, user_settings)


def _render_skills_response(request, chat_id):
    """Re-render the skills card list after a mutation."""
    return _render_skill_card_list(request, chat_id, request.POST)


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance for fuzzy tag matching."""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[len(b)]


def _cosine_similarity(a, b):
    """Cosine similarity between two vectors (lists of floats)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _max_edit_distance_for_term(term: str) -> int:
    """Adaptive edit-distance cutoff by token length.

    Short tokens are noisy, so require near-exact matches.
    """
    n = len((term or "").strip())
    if n <= 4:
        return 0
    if n <= 8:
        return 1
    return 2


def _count_close_token_matches(tag_tokens: set[str], content_tokens: set[str]) -> int:
    """Count tag tokens that have a close-enough match in content tokens."""
    if not tag_tokens or not content_tokens:
        return 0

    matched = 0
    for tag_token in tag_tokens:
        allowed = _max_edit_distance_for_term(tag_token)
        best = min(_edit_distance(tag_token, c) for c in content_tokens)
        if best <= allowed:
            matched += 1
    return matched


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def suggest_tags(request, chat_id):
    """Return tags ranked by relevance to the given skill content.

    Uses embedding similarity when embeddings are available, otherwise falls
    back to keyword matching against tag names.
    """
    content = request.GET.get("content", "").strip()
    lang = request.GET.get("lang", "en")
    if not content:
        return JsonResponse({"tags": []})

    content_lower = content[:2000].lower()

    # Tokenize content for robust keyword overlap (avoid punctuation/stop-word noise)
    token_re = re.compile(r"[a-z0-9]+")
    stop_words = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "de",
        "des",
        "du",
        "for",
        "in",
        "is",
        "la",
        "le",
        "les",
        "of",
        "on",
        "or",
        "the",
        "to",
        "un",
        "une",
        "with",
    }
    content_tokens = {
        t
        for t in token_re.findall(content_lower)
        if len(t) >= 3 and t not in stop_words
    }

    # Check if any tags have embeddings
    tags_qs = SkillTag.objects.annotate(usage_count=Count("skills"))
    has_embeddings = tags_qs.exclude(embedding__isnull=True).exists()

    scored = []

    if has_embeddings:
        # Embedding-based similarity
        try:
            from chat._llm.core import OttoLLM

            llm = OttoLLM(mock_embedding=False)
            content_embedding = llm.embed_model.get_text_embedding(content_lower)
        except Exception:
            logger.exception("Failed to compute embedding for tag suggestions")
            return JsonResponse({"tags": []})

        min_similarity = 1.0 - MAX_EMBEDDING_DISTANCE
        for tag in tags_qs.exclude(embedding__isnull=True):
            sim = _cosine_similarity(content_embedding, tag.embedding)
            if sim < min_similarity:
                continue
            display = tag.name_en if lang == "en" else (tag.name_fr or tag.name_en)
            scored.append(
                {
                    "id": tag.id,
                    "name_en": tag.name_en or "",
                    "name_fr": tag.name_fr or "",
                    "display": display,
                    "count": tag.usage_count,
                    "similarity": round(sim, 4),
                }
            )
        scored.sort(key=lambda x: -x["similarity"])
    else:
        # Keyword fallback — weighted phrase + token overlap matching
        for tag in tags_qs:
            name_en = (tag.name_en or "").lower()
            name_fr = (tag.name_fr or "").lower()

            # Strong signal: exact phrase match
            score = 0
            if name_en and name_en in content_lower:
                score = max(score, 10)
            elif name_fr and name_fr in content_lower:
                score = max(score, 10)

            # Secondary signal: token overlap after normalization/stop-word filtering
            tag_tokens = {
                t
                for t in (token_re.findall(name_en) + token_re.findall(name_fr))
                if len(t) >= 3 and t not in stop_words
            }
            overlap = tag_tokens & content_tokens
            if overlap:
                score += len(overlap)

            # Tertiary signal: fuzzy token proximity with adaptive distance cutoff
            close_matches = _count_close_token_matches(tag_tokens, content_tokens)
            score += close_matches

            if score >= MIN_KEYWORD_SCORE:
                display = tag.name_en if lang == "en" else (tag.name_fr or tag.name_en)
                scored.append(
                    {
                        "id": tag.id,
                        "name_en": tag.name_en or "",
                        "name_fr": tag.name_fr or "",
                        "display": display,
                        "count": tag.usage_count,
                        "similarity": score,
                    }
                )
        scored.sort(key=lambda x: (-x["similarity"], -x["count"]))

    return JsonResponse({"tags": scored[:5]})


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
def tag_autocomplete(request, chat_id):
    """Return JSON list of tags matching a query, sorted by popularity.

    Supports fuzzy matching: if no substring matches found, falls back to
    edit-distance ranking. Also allows creating new tags by returning
    an ``is_new`` flag when the query doesn't match any existing tag.
    """
    q = request.GET.get("q", "").strip()
    lang = request.GET.get("lang", "en")

    # All tags sorted by popularity
    tags_qs = SkillTag.objects.annotate(usage_count=Count("skills")).order_by(
        "-usage_count"
    )

    results = []
    exact_match = False

    if q:
        q_lower = q.lower()
        # Try substring match first (both languages)
        matched = tags_qs.filter(Q(name_en__icontains=q) | Q(name_fr__icontains=q))
        if matched.exists():
            for tag in matched[:20]:
                display = tag.name_en if lang == "en" else (tag.name_fr or tag.name_en)
                if display.lower() == q_lower:
                    exact_match = True
                results.append(
                    {
                        "id": tag.id,
                        "name_en": tag.name_en or "",
                        "name_fr": tag.name_fr or "",
                        "display": display,
                        "count": tag.usage_count,
                    }
                )
        else:
            # Fuzzy fallback — score all tags by edit distance
            candidates = []
            for tag in tags_qs[:100]:
                name_en = (tag.name_en or "").lower()
                name_fr = (tag.name_fr or "").lower()
                dist = min(
                    _edit_distance(q_lower, name_en), _edit_distance(q_lower, name_fr)
                )
                if dist <= max(3, len(q_lower) // 2):
                    display = (
                        tag.name_en if lang == "en" else (tag.name_fr or tag.name_en)
                    )
                    candidates.append(
                        (
                            dist,
                            tag.usage_count,
                            {
                                "id": tag.id,
                                "name_en": tag.name_en or "",
                                "name_fr": tag.name_fr or "",
                                "display": display,
                                "count": tag.usage_count,
                            },
                        )
                    )
            candidates.sort(key=lambda x: (x[0], -x[1]))
            results = [c[2] for c in candidates[:20]]

        # Offer to create a new tag if no exact match
        if not exact_match:
            results.append(
                {
                    "id": None,
                    "display": q,
                    "name_en": q if lang == "en" else "",
                    "name_fr": q if lang == "fr" else "",
                    "count": 0,
                    "is_new": True,
                }
            )
    else:
        # No query — return popular tags
        for tag in tags_qs[:20]:
            display = tag.name_en if lang == "en" else (tag.name_fr or tag.name_en)
            results.append(
                {
                    "id": tag.id,
                    "name_en": tag.name_en or "",
                    "name_fr": tag.name_fr or "",
                    "display": display,
                    "count": tag.usage_count,
                }
            )

    return JsonResponse({"tags": results})


def _get_or_create_skill_data_source(user, skill):
    """Get or create the DataSource (folder) for a skill inside the user's skill library."""
    from librarian.models import DataSource

    # Check if skill already has a data source
    try:
        return skill.data_source
    except DataSource.DoesNotExist:
        pass

    # Ensure user has a skill library
    library = user.skill_library
    if not library:
        library = user.create_skill_library()

    return DataSource.objects.create(
        name=_get_skill_display_title(skill),
        library=library,
        skill=skill,
    )


@permission_required("chat.access_chat", objectgetter(Chat, "chat_id"))
@require_POST
@budget_required
def skill_upload(request, chat_id, skill_id):
    """Upload files to a skill's dedicated folder and auto-add context hints."""
    skill = get_object_or_404(Skill, id=skill_id)

    # Verify user can edit this skill
    can_edit = can_edit_skill(request.user, skill)
    if not can_edit:
        return JsonResponse(
            {"error": _("You don't have permission to edit this skill.")}, status=403
        )

    form = UploadForm(request.POST, request.FILES, prefix="skill")
    if not form.is_valid():
        logger.error("Skill file upload error.", errors=form.errors)
        return JsonResponse(
            {"error": _("There was an error uploading your files.")}, status=400
        )

    saved_files = form.save()
    if not saved_files:
        return JsonResponse({"error": _("No files were uploaded.")}, status=400)

    data_source, new_documents = _attach_saved_files_to_skill(
        user=request.user,
        skill=skill,
        saved_files=saved_files,
    )

    logger.info(
        "Skill file upload complete.",
        skill_id=skill_id,
        num_files=len(new_documents),
        data_source_id=data_source.id,
    )

    # Return updated context hints and a link to manage files
    user_settings = _get_user_settings(request.user)
    return _render_skill_form(request, chat_id, skill, user_settings)
