from __future__ import annotations

import json

from django.db import transaction

from chat_next.models import Skill

COPY_SUFFIX_EN = "copy"
COPY_SUFFIX_FR = "copie"


def _translated_value(skill: Skill, field_name: str, language: str) -> str:
    return (getattr(skill, f"{field_name}_{language}", "") or "").strip()


def _append_copy_suffix(value: str, suffix: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return f"{value} ({suffix})"


def _build_copied_display_names(source_skill: Skill) -> dict[str, str]:
    display_name_en = _append_copy_suffix(
        _translated_value(source_skill, "display_name", "en"), COPY_SUFFIX_EN
    )
    display_name_fr = _append_copy_suffix(
        _translated_value(source_skill, "display_name", "fr"), COPY_SUFFIX_FR
    )

    if display_name_en or display_name_fr:
        return {
            "display_name_en": display_name_en,
            "display_name_fr": display_name_fr,
        }

    fallback_name = (source_skill.display_name or "").strip()
    return {
        "display_name_en": _append_copy_suffix(fallback_name, COPY_SUFFIX_EN),
        "display_name_fr": "",
    }


def _preserve_hint_id_type(original_value, new_id):
    return str(new_id) if isinstance(original_value, str) else new_id


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


def _clone_folder_documents(
    source_data_source, target_data_source, clones_by_source_id
):
    source_docs = list(
        source_data_source.documents.select_related(
            "saved_file", "parent_document"
        ).all()
    )

    for source_doc in source_docs:
        if source_doc.id not in clones_by_source_id:
            clones_by_source_id[source_doc.id] = _clone_document(
                source_doc, target_data_source
            )

    for source_doc in source_docs:
        if not source_doc.parent_document_id:
            continue
        parent_clone = clones_by_source_id.get(source_doc.parent_document_id)
        if not parent_clone:
            continue
        cloned_doc = clones_by_source_id[source_doc.id]
        if cloned_doc.parent_document_id != parent_clone.id:
            cloned_doc.parent_document = parent_clone
            cloned_doc.save(update_fields=["parent_document"])

    return source_docs


def _ensure_cloned_document(source_doc, target_data_source, clones_by_source_id):
    if source_doc.id not in clones_by_source_id:
        clones_by_source_id[source_doc.id] = _clone_document(
            source_doc, target_data_source
        )
    return clones_by_source_id[source_doc.id]


def _process_cloned_documents(cloned_docs):
    processed_clone_ids = set()
    for cloned_doc in cloned_docs:
        if cloned_doc.id in processed_clone_ids or cloned_doc.is_container:
            continue
        if cloned_doc.extracted_text and cloned_doc.status == "SUCCESS":
            processed_clone_ids.add(cloned_doc.id)
            continue
        if cloned_doc.saved_file or cloned_doc.url:
            cloned_doc.process()
            processed_clone_ids.add(cloned_doc.id)


def _dedupe_context_hints(hints: list) -> list:
    deduped = []
    seen = set()
    for hint in hints:
        if isinstance(hint, dict):
            key = json.dumps(hint, sort_keys=True, ensure_ascii=False)
        else:
            key = repr(hint)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(hint)
    return deduped


def _rewrite_context_hints_for_private_copy(
    user, source_skill: Skill, copied_skill: Skill
):
    from librarian.models import DataSource, Document

    copied_hints = []
    target_data_source = None
    cloned_folders = set()
    clones_by_source_id = {}

    def get_target_data_source():
        nonlocal target_data_source
        if target_data_source is None:
            target_data_source = _get_or_create_skill_data_source(user, copied_skill)
        return target_data_source

    for hint in source_skill.context_hints or []:
        if not isinstance(hint, dict):
            copied_hints.append(hint)
            continue

        updated_hint = hint.copy()
        hint_type = updated_hint.get("type")
        raw_hint_id = updated_hint.get("id")

        try:
            hint_id = int(raw_hint_id)
        except (TypeError, ValueError):
            copied_hints.append(updated_hint)
            continue

        if hint_type == "folder":
            source_data_source = (
                DataSource.objects.select_related("library").filter(id=hint_id).first()
            )
            if not source_data_source:
                copied_hints.append(updated_hint)
                continue

            if not source_data_source.library.is_skill_library:
                copied_hints.append(updated_hint)
                continue

            skill_data_source = get_target_data_source()
            if source_data_source.id not in cloned_folders:
                _clone_folder_documents(
                    source_data_source,
                    skill_data_source,
                    clones_by_source_id,
                )
                cloned_folders.add(source_data_source.id)

            updated_hint["id"] = _preserve_hint_id_type(
                raw_hint_id, skill_data_source.id
            )
            updated_hint["name"] = skill_data_source.name
            updated_hint["parent_library_id"] = _preserve_hint_id_type(
                updated_hint.get("parent_library_id"),
                skill_data_source.library_id,
            )
            copied_hints.append(updated_hint)
            continue

        if hint_type == "document":
            source_doc = (
                Document.objects.select_related("data_source", "data_source__library")
                .filter(id=hint_id)
                .first()
            )
            if not source_doc:
                copied_hints.append(updated_hint)
                continue

            skill_data_source = get_target_data_source()
            cloned_doc = clones_by_source_id.get(source_doc.id)
            if cloned_doc is None:
                cloned_doc = _ensure_cloned_document(
                    source_doc,
                    skill_data_source,
                    clones_by_source_id,
                )

            updated_hint["id"] = _preserve_hint_id_type(raw_hint_id, cloned_doc.id)
            updated_hint["name"] = cloned_doc.filename or cloned_doc.title or "Untitled"
            copied_hints.append(updated_hint)
            continue

        copied_hints.append(updated_hint)

    if clones_by_source_id:
        _process_cloned_documents(clones_by_source_id.values())

    return _dedupe_context_hints(copied_hints)


def clone_skill_for_user(*, user, source_skill: Skill):
    copied_display_names = _build_copied_display_names(source_skill)

    with transaction.atomic():
        copied_skill = Skill.objects.create(
            display_name_en=copied_display_names["display_name_en"],
            display_name_fr=copied_display_names["display_name_fr"],
            description_en=getattr(source_skill, "description_en", "") or "",
            description_fr=getattr(source_skill, "description_fr", "") or "",
            short_description_en=getattr(source_skill, "short_description_en", "")
            or "",
            short_description_fr=getattr(source_skill, "short_description_fr", "")
            or "",
            body_en=getattr(source_skill, "body_en", "") or "",
            body_fr=getattr(source_skill, "body_fr", "") or "",
            required_tools=list(source_skill.required_tools or []),
            context_hints=list(source_skill.context_hints or []),
            tags=list(source_skill.tags or []),
            owner=user,
            sharing_option="private",
            is_system=False,
            is_featured=False,
            load_count=0,
        )
        copied_skill.skill_tags.set(source_skill.skill_tags.all())

        rewritten_hints = _rewrite_context_hints_for_private_copy(
            user,
            source_skill,
            copied_skill,
        )
        if rewritten_hints != (copied_skill.context_hints or []):
            copied_skill.context_hints = rewritten_hints
            copied_skill.save(update_fields=["context_hints"])

    return copied_skill
