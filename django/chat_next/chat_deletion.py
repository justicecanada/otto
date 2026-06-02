from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from structlog import get_logger

logger = get_logger(__name__)


@dataclass
class SkillReferenceMatch:
    skill_id: int
    folder_referenced: bool
    document_ids: set[int]


def _normalize_hint_id(raw_id) -> int | None:
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


def _get_chat_from_data_source(data_source):
    """Return the chat/chat_next object that owns the data source, if any."""
    return getattr(data_source, "chat_next", None) or getattr(data_source, "chat", None)


def _clone_document(source_doc, target_data_source):
    from librarian.models import Document

    cloned_status = source_doc.status
    cloned_status_details = source_doc.status_details
    if source_doc.is_container:
        cloned_status = "SUCCESS"
        cloned_status_details = source_doc.status_details
    elif source_doc.extracted_text:
        cloned_status = "SUCCESS"
        cloned_status_details = ""
    elif cloned_status in {"PROCESSING", "TEXT_EXTRACTED", "INIT"}:
        cloned_status = "PENDING"
        cloned_status_details = ""

    return Document.objects.create(
        data_source=target_data_source,
        status=cloned_status,
        status_details=cloned_status_details,
        usd_cost=source_doc.usd_cost,
        saved_file=source_doc.saved_file,
        filename=source_doc.filename,
        provenance=source_doc.provenance,
        extracted_title=source_doc.extracted_title,
        extracted_modified_at=source_doc.extracted_modified_at,
        generated_title=source_doc.generated_title,
        generated_description=source_doc.generated_description,
        manual_title=source_doc.manual_title,
        extracted_text=source_doc.extracted_text,
        num_chunks=source_doc.num_chunks,
        url=source_doc.url,
        selector=source_doc.selector,
        fetched_at=source_doc.fetched_at,
        url_content_type=source_doc.url_content_type,
        file_path=source_doc.file_path,
        parent_document=None,
        pdf_extraction_method=source_doc.pdf_extraction_method,
        is_container=source_doc.is_container,
    )


def _get_or_create_skill_data_source(user, skill):
    from librarian.models import DataSource

    try:
        return skill.data_source
    except DataSource.DoesNotExist:
        pass

    library = user.skill_library
    if not library:
        library = user.create_skill_library()

    return DataSource.objects.create(
        name=skill.display_name or "Untitled skill",
        library=library,
        skill=skill,
    )


def _get_or_create_cloned_document(source_doc, target_data_source, clones_by_key):
    clone_key = (target_data_source.id, source_doc.id)
    if clone_key in clones_by_key:
        return clones_by_key[clone_key]

    cloned_doc = _clone_document(source_doc, target_data_source)
    clones_by_key[clone_key] = cloned_doc
    return cloned_doc


def _hint_matches_source_folder(hint, data_source, normalized_hint_id=None):
    hint_type = hint.get("type")
    if hint_type != "folder":
        return False

    hint_id = normalized_hint_id
    if hint_id is None:
        hint_id = _normalize_hint_id(hint.get("id"))
    parent_library_id = _normalize_hint_id(hint.get("parent_library_id"))

    return hint_id == data_source.id or (
        parent_library_id == data_source.library_id
        and hint.get("name") == data_source.name
    )


def preserve_skill_referenced_chat_files(data_source) -> dict:
    """Preserve chat-backed files referenced by skills before a data source is deleted.

    Behavior:
    - If a skill references the data source folder, preserve the entire folder.
    - If skills reference individual documents in the data source, preserve those docs.
    - Preserve content in the owning chat user's skill library and rewrite affected hints.
    - Re-process migrated non-container docs so they become searchable in the new library.
    """
    from librarian.models import Document

    from chat_next.models import Skill

    chat = _get_chat_from_data_source(data_source)
    if not chat or not getattr(chat, "user", None):
        return {"migrated": False, "reason": "no_data_source"}

    source_docs = list(
        data_source.documents.select_related("saved_file", "parent_document").all()
    )
    source_doc_ids = {doc.id for doc in source_docs}

    affected_skills: list[SkillReferenceMatch] = []
    folder_referenced = False
    referenced_doc_ids: set[int] = set()

    skills = Skill.objects.exclude(context_hints=[])
    for skill in skills:
        matched_folder = False
        matched_docs: set[int] = set()
        for hint in skill.context_hints or []:
            if not isinstance(hint, dict):
                continue
            hint_type = hint.get("type")
            hint_id = _normalize_hint_id(hint.get("id"))
            if _hint_matches_source_folder(hint, data_source, hint_id):
                matched_folder = True
            elif hint_type == "document" and hint_id in source_doc_ids:
                matched_docs.add(hint_id)

        if matched_folder or matched_docs:
            affected_skills.append(
                SkillReferenceMatch(
                    skill_id=skill.id,
                    folder_referenced=matched_folder,
                    document_ids=matched_docs,
                )
            )
            folder_referenced = folder_referenced or matched_folder
            referenced_doc_ids.update(matched_docs)

    if not affected_skills:
        return {"migrated": False, "reason": "no_skill_references"}

    skill_map = Skill.objects.in_bulk([match.skill_id for match in affected_skills])
    clones_by_key: dict[tuple[int, int], Document] = {}
    cloned_docs_by_skill_id: dict[int, dict[int, Document]] = defaultdict(dict)
    processed_clone_ids: set[int] = set()
    updated_skills = 0

    for match in affected_skills:
        skill = skill_map.get(match.skill_id)
        if not skill:
            continue

        target_data_source = _get_or_create_skill_data_source(chat.user, skill)
        docs_to_clone = (
            source_docs
            if match.folder_referenced
            else [doc for doc in source_docs if doc.id in match.document_ids]
        )

        if not docs_to_clone:
            continue

        cloned_by_source_id = cloned_docs_by_skill_id[skill.id]
        for source_doc in docs_to_clone:
            cloned_by_source_id[source_doc.id] = _get_or_create_cloned_document(
                source_doc,
                target_data_source,
                clones_by_key,
            )

        for source_doc in docs_to_clone:
            if (
                source_doc.parent_document_id
                and source_doc.parent_document_id in cloned_by_source_id
            ):
                cloned_doc = cloned_by_source_id[source_doc.id]
                parent_clone = cloned_by_source_id[source_doc.parent_document_id]
                if cloned_doc.parent_document_id != parent_clone.id:
                    cloned_doc.parent_document = parent_clone
                    cloned_doc.save(update_fields=["parent_document"])

        for cloned_doc in cloned_by_source_id.values():
            if cloned_doc.id in processed_clone_ids or cloned_doc.is_container:
                continue
            if cloned_doc.extracted_text and cloned_doc.status == "SUCCESS":
                processed_clone_ids.add(cloned_doc.id)
                continue
            if cloned_doc.saved_file or cloned_doc.url:
                cloned_doc.process()
                processed_clone_ids.add(cloned_doc.id)

        changed = False
        new_hints = []
        for hint in skill.context_hints or []:
            if not isinstance(hint, dict):
                new_hints.append(hint)
                continue

            updated_hint = hint.copy()
            hint_type = updated_hint.get("type")
            hint_id = _normalize_hint_id(updated_hint.get("id"))

            if _hint_matches_source_folder(updated_hint, data_source, hint_id):
                updated_hint["id"] = (
                    str(target_data_source.id)
                    if isinstance(hint.get("id"), str)
                    else target_data_source.id
                )
                updated_hint["name"] = target_data_source.name
                changed = True
            elif hint_type == "document" and hint_id in cloned_by_source_id:
                cloned_doc = cloned_by_source_id[hint_id]
                updated_hint["id"] = (
                    str(cloned_doc.id)
                    if isinstance(hint.get("id"), str)
                    else cloned_doc.id
                )
                updated_hint["name"] = (
                    cloned_doc.filename or cloned_doc.title or "Untitled"
                )
                changed = True

            new_hints.append(updated_hint)

        if changed:
            skill.context_hints = new_hints
            skill.save(update_fields=["context_hints"])
            updated_skills += 1

    logger.info(
        "Preserved skill-referenced chat files before chat deletion",
        chat_id=str(chat.id),
        source_data_source_id=data_source.id,
        target_data_source_ids=sorted(
            {
                doc.data_source_id
                for skill_docs in cloned_docs_by_skill_id.values()
                for doc in skill_docs.values()
            }
        ),
        migrated_document_count=len(clones_by_key),
        updated_skill_count=updated_skills,
        folder_referenced=folder_referenced,
    )

    return {
        "migrated": True,
        "target_data_source_ids": sorted(
            {
                doc.data_source_id
                for skill_docs in cloned_docs_by_skill_id.values()
                for doc in skill_docs.values()
            }
        ),
        "migrated_document_ids": sorted({doc.id for doc in clones_by_key.values()}),
        "updated_skill_count": updated_skills,
        "folder_referenced": folder_referenced,
    }
