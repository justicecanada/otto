from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils.translation import gettext as _

import yaml

from librarian.models import DataSource, Document, Library

BROKEN_CONTEXT_HINT_MESSAGE = _("Broken link - please re-upload or re-select.")
GLOBAL_SKILL_LIBRARY_NAMES_EN = ("Skill files (Otto defaults)", "Skills Files")


def _skill_library_q(prefix: str = "") -> Q:
    """Return a queryset filter covering user skill libraries and global defaults."""

    def field(suffix: str) -> str:
        return f"{prefix}{suffix}" if prefix else suffix

    return (
        Q(**{field("is_skill_library"): True})
        | Q(**{field("name_en__in"): GLOBAL_SKILL_LIBRARY_NAMES_EN})
        | Q(**{field("name__in"): GLOBAL_SKILL_LIBRARY_NAMES_EN})
    )


@lru_cache(maxsize=1)
def _get_skills_library_lookup_specs() -> dict[str, dict]:
    """Return lookup-key metadata derived from `skills_library.yaml`.

    Fixture-backed default skills may still persist `lookup_key` in their
    `context_hints` if they were created outside `reset_app_data` or predate the
    lookup patching step. This registry lets runtime code resolve those hints to
    real document/folder IDs without mutating stored data.
    """
    yaml_file_path = (
        Path(settings.BASE_DIR) / "librarian" / "fixtures" / "skills_library.yaml"
    )

    try:
        with yaml_file_path.open("r", encoding="utf-8") as yaml_file:
            libraries_data = yaml.safe_load(yaml_file) or []
    except (OSError, yaml.YAMLError):
        return {}

    specs: dict[str, dict] = {}

    for item in libraries_data:
        if item.get("model") != "librarian.library":
            continue

        library_fields = item.get("fields", {}) or {}
        library_names = [
            value
            for value in (
                library_fields.get("name"),
                library_fields.get("name_en"),
                library_fields.get("name_fr"),
            )
            if value
        ]

        for data_source in library_fields.get("data_sources", []) or []:
            ds_fields = data_source.get("fields", {}) or {}
            folder_names = [
                value
                for value in (
                    ds_fields.get("name"),
                    ds_fields.get("name_en"),
                    ds_fields.get("name_fr"),
                )
                if value
            ]
            ds_key = ds_fields.get("key")
            if ds_key:
                specs[ds_key] = {
                    "type": "folder",
                    "library_names": library_names,
                    "folder_names": folder_names,
                }

            for document in ds_fields.get("documents", []) or []:
                doc_fields = document.get("fields", {}) or {}
                doc_key = doc_fields.get("key")
                if not doc_key:
                    continue

                specs[doc_key] = {
                    "type": "document",
                    "library_names": library_names,
                    "folder_names": folder_names,
                    "filename": doc_fields.get("filename"),
                }

    return specs


def _resolve_lookup_key_context_hint(hint_type: str, lookup_key: str) -> dict | None:
    """Resolve a fixture `lookup_key` hint to a concrete runtime resource."""
    spec = _get_skills_library_lookup_specs().get(str(lookup_key or "").strip())
    if not spec or spec.get("type") != hint_type:
        return None

    folder_names = [name for name in spec.get("folder_names", []) if name]
    library_names = [name for name in spec.get("library_names", []) if name]

    if hint_type == "folder":
        query = DataSource.objects.filter(_skill_library_q())
        if library_names:
            query = query.filter(
                Q(library__name__in=library_names)
                | Q(library__name_en__in=library_names)
                | Q(library__name_fr__in=library_names)
            )
        if folder_names:
            query = query.filter(
                Q(name__in=folder_names)
                | Q(name_en__in=folder_names)
                | Q(name_fr__in=folder_names)
            )

        data_source = query.order_by("id").first()
        if not data_source:
            return None

        return {
            "id": data_source.id,
            "name": data_source.name,
        }

    if hint_type == "document":
        filename = spec.get("filename")
        if not filename:
            return None

        query = Document.objects.filter(
            filename=filename,
        ).filter(_skill_library_q("data_source__library__"))
        if library_names:
            query = query.filter(
                Q(data_source__library__name__in=library_names)
                | Q(data_source__library__name_en__in=library_names)
                | Q(data_source__library__name_fr__in=library_names)
            )
        if folder_names:
            query = query.filter(
                Q(data_source__name__in=folder_names)
                | Q(data_source__name_en__in=folder_names)
                | Q(data_source__name_fr__in=folder_names)
            )

        document = query.order_by("id").first()
        if not document:
            return None

        return {
            "id": document.id,
            "name": document.filename or document.title or "Untitled",
        }

    return None


def _normalize_runtime_context_hint(hint: dict) -> dict | None:
    """Normalize one runtime context hint, resolving fixture lookup keys when needed."""
    if not isinstance(hint, dict):
        return None

    hint_type = str(hint.get("type") or "").strip()
    if not hint_type:
        return None

    hint_id = hint.get("id")
    resolved = None
    if hint_id not in (None, ""):
        resolved = {"id": str(hint_id).strip()}
    else:
        lookup_key = hint.get("lookup_key")
        if lookup_key:
            resolved = _resolve_lookup_key_context_hint(hint_type, lookup_key)

    if not resolved or resolved.get("id") in (None, ""):
        return None

    normalized = {
        **hint,
        "type": hint_type,
        "id": str(resolved["id"]).strip(),
    }
    if resolved.get("name") and not normalized.get("name"):
        normalized["name"] = resolved["name"]

    return normalized


def _get_valid_tool_context_hint_ids() -> frozenset[str]:
    """Return the currently valid top-level tool-context-hint ids.

    Imported lazily to avoid circular imports during Django app/model loading.
    """
    from chat_next.models import AVAILABLE_TOOL_IDS, TOOL_CATEGORY_SKILLS

    return frozenset({*AVAILABLE_TOOL_IDS, TOOL_CATEGORY_SKILLS})


def is_valid_tool_context_hint_id(tool_id) -> bool:
    """Return whether ``tool_id`` is a supported tool-category context hint.

    Context hints may only reference top-level tool categories that can be
    safely sent in the Responses API ``tools`` list. Granular function names
    such as ``get_document_text`` are not valid tool context hints.
    """
    return str(tool_id or "").strip() in _get_valid_tool_context_hint_ids()


def sanitize_runtime_context_hints(context_hints):
    """Return context hints safe to surface in prompts and outgoing requests.

    Invalid tool hints are dropped proactively so stale or malformed skills do
    not inject unsupported tool IDs into model instructions or API payloads.
    Non-tool hints are preserved as-is (after light shape normalization) so the
    UI and prompt layer can still honor resource hints.
    """
    sanitized = []

    for hint in context_hints or []:
        normalized = _normalize_runtime_context_hint(hint)
        if not normalized:
            continue

        if normalized["type"] == "tool" and not is_valid_tool_context_hint_id(
            normalized["id"]
        ):
            continue

        sanitized.append(normalized)

    return sanitized


def check_context_hints(context_hints):
    """Return status metadata for persisted context hints.

    The return value is a dict keyed by ``"<type>:<id>"``. Each value is a
    small status object such as ``{"broken": True, "message": "..."}``.
    This keeps validation reusable for UI surfaces without mutating the stored
    context hints themselves.
    """
    context_hints = context_hints or []
    statuses = {}

    library_ids = set()
    folder_ids = set()
    document_ids = set()
    skill_ids = set()

    normalized_hints = []
    for hint in context_hints:
        normalized_hint = _normalize_runtime_context_hint(hint)
        if not normalized_hint:
            continue

        hint_type = normalized_hint["type"]
        hint_id_str = normalized_hint["id"]
        normalized_hints.append((hint_type, hint_id_str))

        if hint_type == "library":
            try:
                library_ids.add(int(hint_id_str))
            except (TypeError, ValueError):
                pass
        elif hint_type == "folder":
            try:
                folder_ids.add(int(hint_id_str))
            except (TypeError, ValueError):
                pass
        elif hint_type == "document":
            try:
                document_ids.add(int(hint_id_str))
            except (TypeError, ValueError):
                pass
        elif hint_type == "skill":
            try:
                skill_ids.add(int(hint_id_str))
            except (TypeError, ValueError):
                pass

    existing_libraries = {
        library.id: library
        for library in Library.objects.filter(id__in=library_ids).only(
            "id", "is_public"
        )
    }
    existing_data_sources = {
        data_source.id: data_source
        for data_source in DataSource.objects.filter(id__in=folder_ids)
        .select_related("library")
        .only("id", "library__is_public")
    }
    existing_document_ids = set(
        Document.objects.filter(id__in=document_ids).values_list("id", flat=True)
    )
    from chat_next.models import Skill

    existing_skill_ids = set(
        Skill.objects.filter(id__in=skill_ids).values_list("id", flat=True)
    )

    for hint_type, hint_id_str in normalized_hints:
        key = f"{hint_type}:{hint_id_str}"
        broken = False
        is_public = False

        if hint_type == "tool":
            broken = not is_valid_tool_context_hint_id(hint_id_str)
        elif hint_type == "library":
            try:
                library = existing_libraries.get(int(hint_id_str))
                broken = library is None
                is_public = bool(library and library.is_public)
            except (TypeError, ValueError):
                broken = True
        elif hint_type == "folder":
            try:
                data_source = existing_data_sources.get(int(hint_id_str))
                broken = data_source is None
                is_public = bool(
                    data_source
                    and data_source.library
                    and data_source.library.is_public
                )
            except (TypeError, ValueError):
                broken = True
        elif hint_type == "document":
            try:
                broken = int(hint_id_str) not in existing_document_ids
            except (TypeError, ValueError):
                broken = True
        elif hint_type == "skill":
            try:
                broken = int(hint_id_str) not in existing_skill_ids
            except (TypeError, ValueError):
                broken = True

        statuses[key] = {
            "broken": broken,
            "message": BROKEN_CONTEXT_HINT_MESSAGE if broken else "",
            "is_public": is_public,
        }

    return statuses
