from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.db import transaction

from chat_next.models import Chat as NextChat
from structlog import get_logger

from otto.models import (
    Cost,
    CostGroup,
    Feedback,
    Notification,
    User,
    UserOptions,
    Visitor,
)
from otto.secure_models import AccessControl

from chat.models import Chat as LegacyChat
from chat.models import Preset as LegacyPreset
from laws.search_history.models import LawSearch
from librarian.models import Library, LibraryUserRole

logger = get_logger(__name__)

ROLE_RANK = {"viewer": 1, "contributor": 2, "admin": 3}
TRANSFER_MODELS = [
    (LegacyChat, "user", "Legacy chats"),
    (NextChat, "user", "AI assistant chats"),
    (LegacyPreset, "owner", "Legacy preset ownership"),
    (Notification, "user", "Notifications"),
    (LawSearch, "user", "Law searches"),
    (Feedback, "created_by", "Feedback created_by"),
    (Feedback, "modified_by", "Feedback modified_by"),
    (Cost, "user", "Costs"),
]
PRESET_PERMISSION_FIELDS = [
    (LegacyPreset, "accessible_to", "Legacy preset access"),
    (LegacyPreset, "editable_by", "Legacy preset edit access"),
]


@dataclass
class UserMergeReportSection:
    title: str
    items: list[str]


@dataclass
class UserMergeResult:
    target: User
    source_users: list[User]
    report_lines: list[str]
    report_sections: list[UserMergeReportSection]


class UserMergeError(ValueError):
    """Raised when a user merge request is invalid."""


def _normalize_sources(target: User, source_users: Iterable[User]) -> list[User]:
    sources_by_id = {}
    for source in source_users:
        if source.pk == target.pk:
            raise UserMergeError("Target user cannot also be selected as a source.")
        sources_by_id[source.pk] = source

    normalized_sources = list(sources_by_id.values())
    if not normalized_sources:
        raise UserMergeError("At least one source user must be selected.")
    return normalized_sources


def build_user_merge_preview(target: User, source_users: Iterable[User]) -> dict:
    normalized_sources = _normalize_sources(target, source_users)

    per_source = []
    for source in normalized_sources:
        target_library_ids = set(
            LibraryUserRole.objects.filter(user=target).values_list(
                "library_id", flat=True
            )
        )
        target_access_keys = set(
            AccessControl.objects.filter(user=target).values_list(
                "content_type_id", "object_id"
            )
        )
        source_summary = {
            "source": source,
            "group_count": source.groups.count(),
            "permission_count": source.user_permissions.count(),
            "cost_group_count": CostGroup.objects.filter(users=source).count(),
            "transfer_counts": [
                {
                    "label": label,
                    "count": Model.objects.filter(**{field: source}).count(),
                }
                for Model, field, label in TRANSFER_MODELS
            ],
            "non_personal_libraries": Library.objects.filter(
                created_by=source, is_personal_library=False
            ).count(),
            "access_control_modified": AccessControl.objects.filter(
                modified_by=source
            ).count(),
            "preset_permission_counts": [
                {
                    "label": label,
                    "count": Model.objects.filter(**{field: source}).count(),
                }
                for Model, field, label in PRESET_PERMISSION_FIELDS
            ],
            "personal_library_count": Library.objects.filter(
                created_by=source, is_personal_library=True
            ).count(),
            "library_role_count": LibraryUserRole.objects.filter(user=source).count(),
            "library_role_conflicts": LibraryUserRole.objects.filter(
                user=source, library_id__in=target_library_ids
            ).count(),
            "access_control_count": AccessControl.objects.filter(user=source).count(),
            "access_control_conflicts": sum(
                1
                for key in AccessControl.objects.filter(user=source).values_list(
                    "content_type_id", "object_id"
                )
                if key in target_access_keys
            ),
            "has_user_options": UserOptions.objects.filter(user=source).exists(),
            "has_visitor": Visitor.objects.filter(user=source).exists(),
        }
        per_source.append(source_summary)

    return {
        "target": target,
        "sources": normalized_sources,
        "per_source": per_source,
    }


@transaction.atomic
def merge_users(target: User, source_users: Iterable[User], actor: User | None = None):
    normalized_sources = _normalize_sources(target, source_users)
    target = User.objects.select_for_update().get(pk=target.pk)
    sources = list(
        User.objects.select_for_update().filter(
            pk__in=[source.pk for source in normalized_sources]
        )
    )

    if len(sources) != len(normalized_sources):
        raise UserMergeError("One or more source users could not be loaded.")

    report_lines: list[str] = []
    logger.info(
        "Starting user merge",
        actor_upn=getattr(actor, "upn", None),
        target_id=target.pk,
        target_upn=target.upn,
        source_ids=[source.pk for source in sources],
        source_upns=[source.upn for source in sources],
    )

    for source in sources:
        report_lines.extend(_merge_single_source(target, source))

    logger.info(
        "Completed user merge",
        actor_upn=getattr(actor, "upn", None),
        target_id=target.pk,
        target_upn=target.upn,
        source_ids=[source.pk for source in sources],
        source_upns=[source.upn for source in sources],
    )
    report_sections = _build_report_sections(report_lines)
    return UserMergeResult(
        target=target,
        source_users=sources,
        report_lines=report_lines,
        report_sections=report_sections,
    )


def _build_report_sections(report_lines: Iterable[str]) -> list[UserMergeReportSection]:
    sections: list[UserMergeReportSection] = []
    current_title: str | None = None
    current_items: list[str] = []

    for line in report_lines:
        if not line:
            if current_title or current_items:
                sections.append(
                    UserMergeReportSection(
                        title=current_title or "Merge details",
                        items=current_items,
                    )
                )
            current_title = None
            current_items = []
            continue

        if line.startswith("===") and line.endswith("==="):
            if current_title or current_items:
                sections.append(
                    UserMergeReportSection(
                        title=current_title or "Merge details",
                        items=current_items,
                    )
                )
                current_items = []
            current_title = line.strip("= ")
            continue

        current_items.append(line)

    if current_title or current_items:
        sections.append(
            UserMergeReportSection(
                title=current_title or "Merge details",
                items=current_items,
            )
        )

    return sections


def _merge_single_source(target: User, source: User) -> list[str]:
    report_lines = [f"=== {source.upn} ({source.id}) -> {target.upn} ({target.id}) ==="]

    target.groups.add(*source.groups.all())
    target.user_permissions.add(*source.user_permissions.all())
    for cost_group in CostGroup.objects.filter(users=source):
        cost_group.users.add(target)

    if not target.accepted_terms_date and source.accepted_terms_date:
        target.accepted_terms_date = source.accepted_terms_date
    for field_name in [
        "homepage_tour_completed",
        "ai_assistant_tour_completed",
        "laws_search_tour_completed",
    ]:
        setattr(
            target,
            field_name,
            getattr(target, field_name) or getattr(source, field_name),
        )
    if not target.default_preset_id and source.default_preset_id:
        target.default_preset_id = source.default_preset_id
    if not target.chat_next_default_preset_id and source.chat_next_default_preset_id:
        target.chat_next_default_preset_id = source.chat_next_default_preset_id
    target.save()

    report_lines.append(
        f"Roles/permissions/cost groups merged: {source.groups.count()}/{source.user_permissions.count()}/{CostGroup.objects.filter(users=source).count()}"
    )

    _transfer_direct_relations(target, source, report_lines)
    _transfer_preset_permissions(target, source, report_lines)
    _merge_personal_libraries(target, source, report_lines)
    _merge_library_roles(target, source, report_lines)
    _merge_access_controls(target, source, report_lines)
    _merge_singletons(target, source, report_lines)

    source.is_active = False
    source.save(update_fields=["is_active", "upn", "email"])
    report_lines.append(f"Source inactive: {source.upn}")
    report_lines.append("")
    return report_lines


def _transfer_direct_relations(target: User, source: User, report_lines: list[str]):
    for Model, field, label in TRANSFER_MODELS:
        moved_count = Model.objects.filter(**{field: source}).update(**{field: target})
        report_lines.append(f"{label}: {moved_count}")

    library_count = Library.objects.filter(
        created_by=source, is_personal_library=False
    ).update(created_by=target)
    report_lines.append(f"Shared library ownership: {library_count}")

    modified_count = AccessControl.objects.filter(modified_by=source).update(
        modified_by=target
    )
    report_lines.append(f"AccessControl.modified_by: {modified_count}")


def _transfer_preset_permissions(target: User, source: User, report_lines: list[str]):
    for Model, field, label in PRESET_PERMISSION_FIELDS:
        rows = list(Model.objects.filter(**{field: source}))
        for row in rows:
            getattr(row, field).add(target)
            getattr(row, field).remove(source)
        report_lines.append(f"{label}: {len(rows)}")


def _merge_personal_libraries(target: User, source: User, report_lines: list[str]):
    target_personal = Library.objects.filter(
        created_by=target, is_personal_library=True
    ).first()
    source_personals = list(
        Library.objects.filter(created_by=source, is_personal_library=True)
    )
    for library in source_personals:
        if not target_personal:
            library.created_by = target
            library.save(update_fields=["created_by"])
            target_personal = library
        LibraryUserRole.objects.update_or_create(
            library=library,
            user=target,
            defaults={"role": "admin"},
        )

    report_lines.append(f"Personal libraries touched: {len(source_personals)}")


def _merge_library_roles(target: User, source: User, report_lines: list[str]):
    moved = upgraded = deleted = 0
    for row in list(
        LibraryUserRole.objects.filter(user=source).select_related("library")
    ):
        other = LibraryUserRole.objects.filter(user=target, library=row.library).first()
        if other:
            if ROLE_RANK[row.role] > ROLE_RANK[other.role]:
                other.role = row.role
                other.save(update_fields=["role"])
                upgraded += 1
            row.delete()
            deleted += 1
        else:
            row.user = target
            row.save(update_fields=["user"])
            moved += 1

    report_lines.append(
        f"LibraryUserRole moved/upgraded/deleted: {moved}/{upgraded}/{deleted}"
    )


def _merge_access_controls(target: User, source: User, report_lines: list[str]):
    moved = merged = dropped = 0
    for row in list(AccessControl.objects.filter(user=source)):
        other = AccessControl.objects.filter(
            user=target,
            content_type=row.content_type,
            object_id=row.object_id,
        ).first()

        if other:
            AccessControl.objects.filter(pk=other.pk).update(
                can_view=other.can_view or row.can_view,
                can_change=other.can_change or row.can_change,
                can_delete=other.can_delete or row.can_delete,
                modified_by_id=other.modified_by_id or row.modified_by_id,
                reason=other.reason or row.reason,
            )
            AccessControl.objects.filter(pk=row.pk).delete()
            merged += 1
            continue

        try:
            row.content_type.get_object_for_this_type(pk=row.object_id)
            AccessControl.objects.filter(pk=row.pk).update(user=target)
            moved += 1
        except Exception:
            AccessControl.objects.filter(pk=row.pk).delete()
            dropped += 1

    report_lines.append(
        f"AccessControl moved/merged/dropped: {moved}/{merged}/{dropped}"
    )


def _merge_singletons(target: User, source: User, report_lines: list[str]):
    source_options = UserOptions.objects.filter(user=source).first()
    target_options = UserOptions.objects.filter(user=target).first()
    if source_options and not target_options:
        source_options.user = target
        source_options.save(update_fields=["user"])
        report_lines.append("UserOptions: moved")
    elif source_options and target_options:
        UserOptions.objects.filter(pk=source_options.pk).delete()
        report_lines.append("UserOptions: kept target, dropped source duplicate")
    else:
        report_lines.append("UserOptions: nothing")

    source_visitor = Visitor.objects.filter(user=source).first()
    target_visitor = Visitor.objects.filter(user=target).first()
    if source_visitor and not target_visitor:
        source_visitor.user = target
        source_visitor.save(update_fields=["user"])
        report_lines.append("Visitor: moved")
    elif source_visitor and target_visitor:
        Visitor.objects.filter(pk=source_visitor.pk).delete()
        report_lines.append("Visitor: kept target, dropped source duplicate")
    else:
        report_lines.append("Visitor: nothing")
