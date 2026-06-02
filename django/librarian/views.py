import os
from dataclasses import dataclass
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.db.models import Case, Count, IntegerField, Q, Sum, Value, When
from django.db.models.functions import Coalesce, Lower
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _

from data_fetcher.extras import cache_within_request
from data_fetcher.util import clear_request_caches
from rules.contrib.views import objectgetter
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from otto.utils.common import display_cad_cost, generate_mailto
from otto.utils.decorators import budget_required, permission_required

from chat.forms import UploadForm
from chat.utils import create_library_shared_notification
from librarian.utils.process_engine import generate_hash, sanitize_content_type

from .forms import (
    DataSourceDetailForm,
    DocumentDetailForm,
    LibraryDetailForm,
    LibraryUsersForm,
)
from .models import DataSource, Document, Library, LibraryUserRole, SavedFile

logger = get_logger(__name__)
IN_PROGRESS_STATUSES = ["PENDING", "INIT", "PROCESSING", "TEXT_EXTRACTED"]
END_STATUSES = ["SUCCESS", "ERROR", "BLOCKED", "PAUSED"]
# Pagination for document lists
DOCUMENTS_PER_PAGE = 50
# Disable polling for folders with more than this many documents (performance)
# Set to match pagination limit so polling only affects single page
MAX_DOCUMENTS_FOR_POLLING = DOCUMENTS_PER_PAGE


def _is_chat_uploads_folder(data_source: DataSource | None) -> bool:
    """Return True when this folder should be read-only in Manage Libraries.

    Only chat_next folders are view-only. Legacy chat folders must remain editable
    because they still use librarian upload/create/delete flows.
    """
    return bool(data_source and data_source.chat_next_id)


# ---- Sorting helpers (persist per data source in session) ----
def _set_sort_pref(request, data_source_id: int, key: str):
    prefs = request.session.get("librarian_sort", {})
    prefs[str(data_source_id)] = key
    request.session["librarian_sort"] = prefs
    # Mark the session as modified so Django saves it
    request.session.modified = True


def _get_sort_pref(request, data_source_id: int) -> str:
    prefs = request.session.get("librarian_sort", {})
    # default newest first
    return prefs.get(str(data_source_id), "date_desc")


def _apply_queryset_sort(qs, key: str):
    """Apply server-side ordering to a Document queryset based on the current sort key.

    Uses Django ORM annotations and order_by to push sorting to the database,
    enabling efficient pagination via LIMIT/OFFSET.

    For filetype sorting, we use Coalesce to combine saved_file__content_type
    and url_content_type into a single sortable field.
    """
    if key == "date_desc":
        # Sort by extracted_modified_at if present, otherwise created_at
        return qs.annotate(
            sort_date=Coalesce("extracted_modified_at", "created_at")
        ).order_by("-sort_date", "-id")

    if key == "date_asc":
        return qs.annotate(
            sort_date=Coalesce("extracted_modified_at", "created_at")
        ).order_by("sort_date", "id")

    if key == "filename_asc":
        return qs.annotate(_fname_lower=Lower("filename")).order_by(
            "_fname_lower", "id"
        )

    if key == "filename_desc":
        return qs.annotate(_fname_lower=Lower("filename")).order_by(
            "-_fname_lower", "-id"
        )

    if key == "filetype_asc":
        # Combine saved_file.content_type and url_content_type, prefer saved_file
        return (
            qs.select_related("saved_file")
            .annotate(
                _content_type=Coalesce(
                    Lower("saved_file__content_type"), Lower("url_content_type")
                )
            )
            .order_by("_content_type", "id")
        )

    if key == "filetype_desc":
        return (
            qs.select_related("saved_file")
            .annotate(
                _content_type=Coalesce(
                    Lower("saved_file__content_type"), Lower("url_content_type")
                )
            )
            .order_by("-_content_type", "-id")
        )

    if key == "chunks_desc":
        return qs.order_by("-num_chunks", "-id")

    if key == "chunks_asc":
        return qs.order_by("num_chunks", "id")

    if key.startswith("status_"):
        # Map status values to sort order integers
        status_case = Case(
            When(status__in=["INIT", "PROCESSING", "TEXT_EXTRACTED"], then=Value(0)),
            When(status="PENDING", then=Value(1)),
            When(status="PAUSED", then=Value(2)),
            When(status="SUCCESS", then=Value(3)),
            When(status="BLOCKED", then=Value(4)),
            When(status="ERROR", then=Value(5)),
            default=Value(999),
            output_field=IntegerField(),
        )
        qs = qs.annotate(_status_order=status_case)
        if key.endswith("desc"):
            return qs.order_by("-_status_order", "-id")
        else:
            return qs.order_by("_status_order", "id")

    # For unknown keys, return queryset unchanged
    return qs


def _warn_skill_references(request, item_type, item_id, item_name):
    """Add a warning message if any skills reference this library/document/folder."""
    from django.utils.html import format_html, format_html_join

    from otto.rules import get_skills_referencing_item

    referencing_skills = get_skills_referencing_item(item_type, item_id).distinct()
    if referencing_skills.exists():
        skill_names = list(referencing_skills.values_list("display_name", flat=True))
        skill_list = format_html_join(
            ", ", "<strong>{}</strong>", ((n,) for n in skill_names)
        )
        messages.warning(
            request,
            format_html(
                _(
                    'Warning: "{}" was referenced by skill(s): {}. '
                    "Those skills may need their context hints updated."
                ),
                item_name,
                skill_list,
            ),
        )


def _build_data_source_status_summary(status_counts):
    status_counts = status_counts or {}
    return {
        "total": sum(status_counts.values()),
        "pending": status_counts.get("PENDING", 0),
        "queued": status_counts.get("INIT", 0),
        "processing": status_counts.get("PROCESSING", 0),
        "text_extracted": status_counts.get("TEXT_EXTRACTED", 0),
        "paused": status_counts.get("PAUSED", 0),
        "completed": status_counts.get("SUCCESS", 0),
        "errors": status_counts.get("ERROR", 0),
        "stopped": status_counts.get("BLOCKED", 0),
    }


@cache_within_request
def _get_data_source_metrics(data_source_id):
    """Get metrics for a data source, cached per request.

    Args:
        data_source_id: ID of the data source

    Returns:
        Tuple of (total_cost_display, total_chunks, status_counts, status_summary)
    """
    queryset = Document.objects.filter(data_source_id=data_source_id)
    aggregates = queryset.aggregate(
        total_usd=Sum("usd_cost"), total_chunks=Sum("num_chunks")
    )
    status_rows = queryset.values("status").annotate(total=Count("id"))
    status_counts = {
        row["status"]: row["total"] for row in status_rows if row["status"]
    }

    total_usd_val = aggregates.get("total_usd") or 0
    total_chunks_val = aggregates.get("total_chunks") or 0

    if not total_usd_val or total_usd_val == 0:
        total_cost_display = "$0.00"
    else:
        total_cost_display = display_cad_cost(total_usd_val)

    total_chunks = int(total_chunks_val or 0)

    return (
        total_cost_display,
        total_chunks,
        status_counts,
        _build_data_source_status_summary(status_counts),
    )


@cache_within_request
def get_editable_libraries(user):
    """Get all libraries the user can edit, using optimized queries."""
    from django.db.models import Q

    from otto.rules import (
        GLOBAL_SKILL_DEFAULTS_LIBRARY_NAME_EN,
        get_library_roles_for_user,
    )

    admin_or_contrib_libs = {
        role.library_id
        for role in get_library_roles_for_user(user)
        if role.role in ["admin", "contributor"]
    }

    # Check user's global permissions from rules.py
    is_otto_admin = user.groups.filter(name=settings.OTTO_ADMIN_GROUP).exists()
    can_manage_public = user.has_perm("librarian.manage_public_libraries")

    # Build the query
    conditions = Q()

    # 1. Explicit non-system libraries where the user has edit roles.
    if admin_or_contrib_libs:
        conditions |= Q(
            pk__in=admin_or_contrib_libs,
            is_personal_library=False,
            is_skill_library=False,
        )

    # 2. Public libraries:
    public_conditions = Q()
    # a) User is an Otto admin
    if is_otto_admin:
        public_conditions |= Q(
            is_public=True,
            is_personal_library=False,
            is_skill_library=False,
        )
    # b) User can manage public libraries AND is an admin/contributor on the library
    if can_manage_public and admin_or_contrib_libs:
        public_conditions |= Q(
            is_public=True,
            pk__in=admin_or_contrib_libs,
            is_personal_library=False,
            is_skill_library=False,
        )

    conditions |= public_conditions

    # 3. The creator can always edit their own libraries, including personal/skill.
    conditions |= Q(created_by=user)

    # 4. Otto admins can edit the global defaults skill library.
    if is_otto_admin:
        defaults_library_ids = list(
            Library.objects.filter(
                name_en=GLOBAL_SKILL_DEFAULTS_LIBRARY_NAME_EN
            ).values_list("id", flat=True)
        )
        if defaults_library_ids:
            conditions |= Q(pk__in=defaults_library_ids)

    return list(
        Library.objects.filter(conditions)
        .distinct()
        .order_by(
            "-is_personal_library",
            "-is_skill_library",
            "-is_public",
            "order",
            "-created_at",
        )
    )


@cache_within_request
def get_viewable_libraries(user):
    """Get all libraries the user can view, using optimized queries."""
    from django.db.models import Q

    from otto.rules import (
        GLOBAL_SKILL_DEFAULTS_LIBRARY_NAME_EN,
        get_library_roles_for_user,
    )

    user_library_ids = {role.library_id for role in get_library_roles_for_user(user)}

    conditions = Q(
        is_public=True,
        is_personal_library=False,
        is_skill_library=False,
    ) | Q(created_by=user)

    if user_library_ids:
        conditions |= Q(
            pk__in=list(user_library_ids),
            is_personal_library=False,
            is_skill_library=False,
        )

    if user.groups.filter(name=settings.OTTO_ADMIN_GROUP).exists():
        defaults_library_ids = list(
            Library.objects.filter(
                name_en=GLOBAL_SKILL_DEFAULTS_LIBRARY_NAME_EN
            ).values_list("id", flat=True)
        )
        if defaults_library_ids:
            conditions |= Q(pk__in=defaults_library_ids)

    return list(
        Library.objects.filter(conditions).order_by(
            "-is_personal_library",
            "-is_skill_library",
            "-is_public",
            "order",
            "-created_at",
        )
    )


# AC-20: Implements role-based access control for interacting with data sources
def modal_view(request, item_type=None, item_id=None, parent_id=None, documents=None):
    """
    !!! This is not to be called directly, but rather through the wrapper functions
        which implement permission checking (see below) !!!

    This _beastly_ function handles almost all actions in the "Edit libraries" modal.

    This includes the initial view (no library selected), and the subsequent views for
    editing a library, data source, or document; creating each of the same; including
    GET, POST, and DELETE requests. It also handles library user management.

    The modal is updated with the new content after each request.
    When a data source is visible that contains in-progress documents, the modal will
    poll for updates until all documents are processed or stopped.
    """
    import time

    time.sleep(0.3)
    user_id = request.user.id
    active_cost_group = request.user.get_active_cost_group(request)
    cost_group_id = active_cost_group.id if active_cost_group else None
    bind_contextvars(feature="librarian", user_id=user_id, cost_group_id=cost_group_id)

    libraries = get_viewable_libraries(request.user)
    editable_libraries = get_editable_libraries(request.user)
    selected_library = None
    data_sources = None
    selected_data_source = None
    documents = None
    selected_document = None
    form = None
    users_form = None
    show_document_status = False
    focus_el = None
    has_error = False
    total_cost = None
    total_chunks = None
    failed_count = None
    blocked_count = None
    paused_count = None
    data_source_status_summary = None
    data_source_status_counts = {}
    page = 1
    total_pages = 1
    total_document_count = 0

    if item_type == "document":
        if request.method == "POST":
            if not item_id:
                target_data_source = get_object_or_404(
                    DataSource.objects.select_related("library"), id=parent_id
                )
                if _is_chat_uploads_folder(target_data_source):
                    messages.warning(
                        request,
                        _(
                            "Files for chat folders must be added in the chat. "
                            "This folder is view-only in Manage libraries."
                        ),
                    )
                    request.method = "GET"
                    return modal_view(
                        request,
                        item_type="data_source",
                        item_id=target_data_source.id,
                    )
            document = (
                Document.objects.select_related(
                    "data_source", "data_source__library"
                ).get(id=item_id)
                if item_id
                else None
            )
            form = DocumentDetailForm(request.POST, instance=document)
            if form.is_valid():
                form.save()
                messages.success(
                    request,
                    (
                        _("Document updated successfully.")
                        if item_id
                        else _("Document created successfully.")
                    ),
                )
                if not item_id:
                    form.instance.process()
                selected_document = form.instance
                selected_data_source = selected_document.data_source
                item_id = selected_document.id
                show_document_status = True
            else:
                logger.error("Error updating document:", errors=form.errors)
                has_error = True
                selected_data_source = (
                    DataSource.objects.filter(id=parent_id)
                    .select_related("library")
                    .first()
                    or form.instance.data_source
                )
        elif request.method == "DELETE":
            if item_id == 1:
                return HttpResponse(status=400)
            document = get_object_or_404(
                Document.objects.select_related("data_source", "data_source__library"),
                id=item_id,
            )
            selected_data_source = document.data_source
            if _is_chat_uploads_folder(selected_data_source):
                messages.warning(
                    request,
                    _(
                        "Files in chat folders can only be deleted from the chat "
                        "where they were uploaded."
                    ),
                )
                request.method = "GET"
                return modal_view(
                    request,
                    item_type="data_source",
                    item_id=selected_data_source.id,
                )
            _warn_skill_references(request, "document", document.id, document.filename)
            document.delete()
            messages.success(request, _("Document deleted successfully."))
        else:
            if item_id:
                selected_document = get_object_or_404(
                    Document.objects.select_related(
                        "data_source", "data_source__library"
                    ),
                    id=item_id,
                )
                selected_data_source = selected_document.data_source
                show_document_status = True
            else:
                selected_data_source = get_object_or_404(
                    DataSource.objects.select_related("library"), id=parent_id
                )
        # Always fetch, filter by active search (if any), and then apply persisted sort
        active_search = (request.GET.get("search", "") or "").strip()
        qs = selected_data_source.documents.defer("extracted_text").all()
        if active_search:
            qs = qs.filter(
                Q(filename__icontains=active_search)
                | Q(manual_title__icontains=active_search)
                | (
                    Q(extracted_title__icontains=active_search)
                    & (Q(manual_title__isnull=True) | Q(manual_title=""))
                )
            )

        # Apply DB-level sorting
        sort_key = _get_sort_pref(request, selected_data_source.id)
        qs = _apply_queryset_sort(qs, sort_key)

        # Pagination at DB level
        page = int(request.GET.get("page", 1))
        total_document_count = qs.count()
        total_pages = (
            total_document_count + DOCUMENTS_PER_PAGE - 1
        ) // DOCUMENTS_PER_PAGE or 1
        page = max(1, min(page, total_pages))
        start = (page - 1) * DOCUMENTS_PER_PAGE
        end = start + DOCUMENTS_PER_PAGE
        documents = list(qs[start:end])

        selected_library = selected_data_source.library
        data_sources = selected_library.folders
        if not item_id and not request.method == "DELETE":
            new_document = create_temp_object("document")
            documents.insert(0, new_document)
            selected_document = new_document
            focus_el = "#id_url"
        if not request.method == "DELETE":
            form = form or DocumentDetailForm(
                instance=selected_document if item_id else None,
                data_source_id=parent_id,
            )

    if item_type == "data_source":
        if request.method == "POST":
            data_source = (
                DataSource.objects.select_related("library").get(id=item_id)
                if item_id
                else None
            )
            form = DataSourceDetailForm(
                request.POST,
                instance=data_source,
                user=request.user,
            )
            if form.is_valid():
                form.save()
                if item_id:
                    toast_message = _("Folder updated successfully.")
                else:
                    toast_message = _("Folder created successfully.")
                messages.success(request, toast_message)
                selected_data_source = form.instance
                item_id = selected_data_source.id
                selected_library = selected_data_source.library
                documents = selected_data_source.documents.defer("extracted_text").all()
            else:
                logger.error("Error updating folder:", errors=form.errors)
                selected_library = get_object_or_404(Library, id=parent_id)
        elif request.method == "DELETE":
            data_source = get_object_or_404(
                DataSource.objects.select_related("library"), id=item_id
            )
            selected_library = data_source.library
            _warn_skill_references(request, "folder", data_source.id, data_source.name)
            data_source.delete()
            messages.success(request, _("Folder deleted successfully."))
            data_sources = selected_library.folders
        else:
            if item_id:
                selected_data_source = get_object_or_404(
                    DataSource.objects.select_related("library"), id=item_id
                )
                selected_library = selected_data_source.library
                # fetch, optionally filter by search, and sort per preference
                active_search = (request.GET.get("search", "") or "").strip()
                qs = selected_data_source.documents.defer("extracted_text").all()
                if active_search:
                    qs = qs.filter(
                        Q(filename__icontains=active_search)
                        | Q(manual_title__icontains=active_search)
                        | (
                            Q(extracted_title__icontains=active_search)
                            & (Q(manual_title__isnull=True) | Q(manual_title=""))
                        )
                    )

                # Apply DB-level sorting
                sort_key = _get_sort_pref(request, selected_data_source.id)
                qs = _apply_queryset_sort(qs, sort_key)

                # Pagination at DB level
                page = int(request.GET.get("page", 1))
                total_document_count = qs.count()
                total_pages = (
                    total_document_count + DOCUMENTS_PER_PAGE - 1
                ) // DOCUMENTS_PER_PAGE or 1
                page = max(1, min(page, total_pages))
                start = (page - 1) * DOCUMENTS_PER_PAGE
                end = start + DOCUMENTS_PER_PAGE
                documents = list(qs[start:end])
            else:
                selected_library = get_object_or_404(Library, id=parent_id)
        data_sources = list(selected_library.folders)
        if not item_id and not request.method == "DELETE":
            new_data_source = create_temp_object("data_source")
            data_sources.insert(0, new_data_source)
            selected_data_source = new_data_source
            focus_el = "#id_name_en"
        if not request.method == "DELETE":
            form = form or DataSourceDetailForm(
                instance=selected_data_source if item_id else None,
                library_id=parent_id,
                user=request.user,
            )

    if item_type == "library":
        # Track if advanced options were expanded
        advanced_expanded = False
        if request.method == "POST":
            library = Library.objects.get(id=item_id) if item_id else None
            # Access library to update accessed_at so the library's retention window resets.
            # This is not implemented using signals due to risk of introducing recursion
            if item_id:
                library.access()
            # Check if advanced options were expanded
            advanced_expanded = request.POST.get("advanced_expanded") == "true"
            form = LibraryDetailForm(
                request.POST,
                instance=library,
                user=request.user,
                advanced_expanded=advanced_expanded,
            )
            if form.is_valid():
                form.save()
                messages.success(
                    request,
                    (
                        _("Library updated successfully.")
                        if item_id
                        else _("Library created successfully.")
                    ),
                )
                clear_request_caches()
                libraries = get_viewable_libraries(request.user)
                editable_libraries = get_editable_libraries(request.user)
                selected_library = form.instance
                # Refresh the form so "public" checkbox behaves properly
                form = LibraryDetailForm(
                    instance=selected_library,
                    user=request.user,
                    advanced_expanded=advanced_expanded,
                )
                item_id = selected_library.id
                data_sources = selected_library.data_sources.all().prefetch_related(
                    "security_label"
                )
                if request.user.has_perm(
                    "librarian.manage_library_users", selected_library
                ):
                    users_form = LibraryUsersForm(
                        library=selected_library,
                        actor=request.user,
                    )
                # Keep advanced options expanded after successful save
                # (advanced_expanded already set to True above if it was expanded)
            else:
                logger.error("Error updating library:", errors=form.errors)
                has_error = True
                # Keep advanced options expanded when there are form errors
                advanced_expanded = True
        elif request.method == "DELETE":
            library = get_object_or_404(Library, id=item_id)
            _warn_skill_references(request, "library", library.id, str(library))
            library.delete()
            messages.success(request, _("Library deleted successfully."))
            clear_request_caches()
            libraries = get_viewable_libraries(request.user)
            editable_libraries = get_editable_libraries(request.user)
        if not request.method == "DELETE":
            if item_id:
                selected_library = get_object_or_404(
                    Library.objects.prefetch_related("user_roles"),
                    id=item_id,
                )
                data_sources = selected_library.folders
                if request.user.has_perm(
                    "librarian.manage_library_users", selected_library
                ):
                    users_form = LibraryUsersForm(
                        library=selected_library,
                        actor=request.user,
                    )
            elif not selected_library:
                new_library = create_temp_object("library")
                editable_libraries.insert(0, new_library)
                selected_library = new_library
                focus_el = "#id_name_en"
            form = form or LibraryDetailForm(
                instance=selected_library if item_id else None,
                user=request.user,
                advanced_expanded=locals().get("advanced_expanded", False),
            )

    if item_type == "library_users":
        if request.method == "POST":
            selected_library = get_object_or_404(
                Library.objects.prefetch_related("user_roles"),
                id=item_id,
            )
            # Access library to update accessed_at so the library's retention window resets.
            selected_library.access()

            # Capture previous user roles before saving to detect newly shared users
            previous_admins = set(
                selected_library.user_roles.filter(role="admin").values_list(
                    "user_id", flat=True
                )
            )
            previous_contributors = set(
                selected_library.user_roles.filter(role="contributor").values_list(
                    "user_id", flat=True
                )
            )
            previous_viewers = set(
                selected_library.user_roles.filter(role="viewer").values_list(
                    "user_id", flat=True
                )
            )
            previous_all_users = (
                previous_admins | previous_contributors | previous_viewers
            )

            users_form = LibraryUsersForm(
                request.POST,
                library=selected_library,
                actor=request.user,
            )
            if users_form.is_valid():
                users_form.save()

                # Detect newly added users and send notifications
                admins_data = users_form.cleaned_data["admins"]
                contributors_data = users_form.cleaned_data["contributors"]
                viewers_data = users_form.cleaned_data["viewers"]
                current_admins = set(
                    u.id
                    for u in (
                        admins_data["users"]
                        if isinstance(admins_data, dict)
                        else admins_data
                    )
                )
                current_contributors = set(
                    u.id
                    for u in (
                        contributors_data["users"]
                        if isinstance(contributors_data, dict)
                        else contributors_data
                    )
                )
                current_viewers = set(
                    u.id
                    for u in (
                        viewers_data["users"]
                        if isinstance(viewers_data, dict)
                        else viewers_data
                    )
                )

                # Users who are new to the library entirely
                new_admins = current_admins - previous_all_users
                new_contributors = current_contributors - previous_all_users
                new_viewers = current_viewers - previous_all_users

                # Also detect role changes (user was already in library but role changed)
                role_changed_to_admin = (
                    current_admins & previous_all_users
                ) - previous_admins
                role_changed_to_contributor = (
                    current_contributors & previous_all_users
                ) - previous_contributors
                role_changed_to_viewer = (
                    current_viewers & previous_all_users
                ) - previous_viewers

                from django.contrib.auth import get_user_model

                User = get_user_model()

                # Combine new users and role-changed users for notifications
                all_new_admins = (new_admins | role_changed_to_admin) - {
                    request.user.id
                }
                all_new_contributors = (
                    new_contributors | role_changed_to_contributor
                ) - {request.user.id}
                all_new_viewers = (new_viewers | role_changed_to_viewer) - {
                    request.user.id
                }

                newly_shared_user_ids = (
                    all_new_admins | all_new_contributors | all_new_viewers
                )

                if newly_shared_user_ids:
                    newly_shared_users = User.objects.filter(
                        id__in=newly_shared_user_ids
                    )
                    for shared_user in newly_shared_users:
                        if shared_user.id in all_new_admins:
                            role = "admin"
                        elif shared_user.id in all_new_contributors:
                            role = "contributor"
                        else:
                            role = "viewer"
                        create_library_shared_notification(
                            shared_user,
                            selected_library,
                            request.user,
                            role=role,
                        )

                messages.success(request, _("Library users updated successfully."))
            else:
                logger.error("Error updating library users:", errors=users_form.errors)
                has_error = True
            # The change may have resulted in the user losing access to manage library users
            if not request.user.has_perm(
                "librarian.manage_library_users", selected_library
            ):
                users_form = None
            data_sources = selected_library.data_sources.all().prefetch_related(
                "security_label"
            )
            form = LibraryDetailForm(instance=selected_library, user=request.user)
        else:
            return HttpResponse(status=405)

    # Poll for updates when a data source is selected that has in-progress documents
    # OR when the selected document itself is still processing
    # BUT disable polling for very large folders (performance)
    poll = False
    poll_disabled_reason = None
    try:
        # Check if folder is too large for polling
        if selected_data_source:
            total_docs = selected_data_source.documents.count()
            if total_docs > MAX_DOCUMENTS_FOR_POLLING:
                poll = False
                poll_disabled_reason = "folder_too_large"
            else:
                poll = selected_data_source.documents.filter(
                    status__in=IN_PROGRESS_STATUSES
                ).exists()
                # Also poll if the selected document is processing (even if not in the list due to search)
                if (
                    not poll
                    and selected_document
                    and hasattr(selected_document, "status")
                ):
                    poll = (
                        selected_document.status in IN_PROGRESS_STATUSES
                        or selected_document.celery_task_id is not None
                    )
    except Exception:
        poll = False
    # We have to construct the poll URL manually (instead of using request.path)
    # because some views, e.g. document_start, return this view from a different URL
    if poll:
        if selected_document and selected_document.id:
            poll_url = reverse(
                "librarian:document_status",
                kwargs={
                    "document_id": selected_document.id,
                    "data_source_id": selected_data_source.id,
                },
            )
        elif selected_document or (selected_data_source and selected_data_source.id):
            poll_url = reverse(
                "librarian:data_source_status",
                kwargs={"data_source_id": selected_data_source.id},
            )
        # Preserve active search and page during polling so list doesn't reset
        active_search = (request.GET.get("search", "") or "").strip()
        if active_search or page > 1:
            params = {}
            if active_search:
                params["search"] = active_search
            if page > 1:
                params["page"] = page
            poll_url = f"{poll_url}?{urlencode(params)}"
    else:
        poll_url = None

    # Build refresh URL for manual refresh button
    # This preserves the current state (library, folder, document, search, page)
    refresh_url = None
    if selected_document and selected_document.id:
        refresh_url = reverse(
            "librarian:modal_view_document", args=[selected_document.id]
        )
    elif selected_data_source and selected_data_source.id:
        refresh_url = reverse(
            "librarian:modal_view_data_source", args=[selected_data_source.id]
        )
    elif selected_library and selected_library.id:
        refresh_url = reverse(
            "librarian:modal_view_library", args=[selected_library.id]
        )
    else:
        refresh_url = reverse("librarian:modal_library_list")

    # Add search and page params to refresh URL (same logic as poll_url)
    active_search = (request.GET.get("search", "") or "").strip()
    if active_search or page > 1:
        params = {}
        if active_search:
            params["search"] = active_search
        if page > 1:
            params["page"] = page
        refresh_url = f"{refresh_url}?{urlencode(params)}"

    # Don't show chats that don't have any Q&A documents
    if data_sources and selected_library.is_personal_library:
        data_sources = [ds for ds in data_sources if ds.documents.count() > 0]

    # Create view-only libraries list (viewable but not editable)
    view_only_libraries = [lib for lib in libraries if lib not in editable_libraries]

    # Compute totals for the selected data source
    if selected_data_source and getattr(selected_data_source, "id", None):
        try:
            (
                total_cost,
                total_chunks,
                data_source_status_counts,
                data_source_status_summary,
            ) = _get_data_source_metrics(selected_data_source.id)
            failed_count = data_source_status_counts.get("ERROR", 0)
            blocked_count = data_source_status_counts.get("BLOCKED", 0)
            paused_count = data_source_status_counts.get("PAUSED", 0)
        except Exception:
            total_cost = None
            total_chunks = None
            failed_count = None
            blocked_count = None
            paused_count = None
            data_source_status_counts = {}
            data_source_status_summary = None

    show_data_source_status = (
        selected_data_source
        and getattr(selected_data_source, "id", None)
        and not show_document_status
    )

    context = {
        "editable_libraries": editable_libraries,
        "view_only_libraries": view_only_libraries,
        "selected_library": selected_library,
        "data_sources": data_sources,
        "selected_data_source": selected_data_source,
        "documents": documents,
        "selected_document": selected_document,
        "detail_form": form,
        "users_form": users_form,
        "document_status": show_document_status,
        "focus_el": focus_el,
        "poll_url": poll_url,
        "poll_response": "poll" in request.GET,
        "poll_disabled_reason": poll_disabled_reason,
        "has_error": has_error,
        "upload_form": UploadForm(prefix="librarian"),
        "total_cost": total_cost,
        "total_chunks": total_chunks,
        "failed_count": failed_count or 0,
        "blocked_count": blocked_count or 0,
        "paused_count": paused_count or 0,
        "data_source_status_summary": data_source_status_summary,
        "data_source_status_counts": data_source_status_counts,
        "show_data_source_status": bool(show_data_source_status),
        "page": page,
        "total_pages": total_pages,
        "total_document_count": total_document_count,
        "refresh_url": refresh_url,
        "advanced_expanded": locals().get("advanced_expanded", False),
    }

    if documents is not None:
        context["documents"] = documents
        # Keep current search in the input if present
        try:
            # Use None if search param not present (first load), empty string if present but empty
            if "search" in request.GET:
                context["search"] = (request.GET.get("search", "") or "").strip()
            else:
                context["search"] = None
        except Exception:
            pass

    # Expose current sort key for template radios when a data source is selected
    try:
        if selected_data_source:
            context["current_sort"] = _get_sort_pref(request, selected_data_source.id)
    except Exception:
        pass

    return render(request, "librarian/modal_inner.html", context)


@permission_required(
    "librarian.view_data_source", objectgetter(DataSource, "data_source_id")
)
def poll_status(request, data_source_id, document_id=None):
    """
    Polling view for data source status updates
    Updates the document list in the modal with updated titles / status icons
    """
    # Preserve current search from query string so the list doesn't reset on poll
    # Use None if search param not present (first load), otherwise use the value
    if "search" in request.GET:
        search = (request.GET.get("search", "") or "").strip()
    else:
        search = None

    # Optimize by fetching data_source and library in one query
    data_source = (
        DataSource.objects.filter(id=data_source_id).select_related("library").first()
    )
    if not data_source:
        return HttpResponse(status=404)

    base_qs = Document.objects.filter(data_source_id=data_source_id).defer(
        "extracted_text"
    )

    # Get total document count once to avoid multiple queries
    total_docs = base_qs.count()

    documents = base_qs
    if search:
        documents = documents.filter(
            Q(filename__icontains=search)
            | Q(manual_title__icontains=search)
            | (
                Q(extracted_title__icontains=search)
                & (Q(manual_title__isnull=True) | Q(manual_title=""))
            )
        )
    document = None
    if document_id:
        try:
            document = Document.objects.defer("extracted_text").get(id=document_id)
            if document.data_source_id != data_source_id:
                document = None
        except Document.DoesNotExist:
            document = None

    # Current page needs to be read before determining polling URL
    page = int(request.GET.get("page", 1))

    poll = False
    poll_disabled_reason = None
    try:
        # Check total documents for polling threshold (use cached total_docs)
        if total_docs > MAX_DOCUMENTS_FOR_POLLING:
            poll_disabled_reason = "folder_too_large"
        else:
            # Detect if any document is still processing across the full data source.
            poll = base_qs.filter(status__in=IN_PROGRESS_STATUSES).exists()
            if not poll and document is not None:
                # Check if the selected document is processing
                poll = document.status in IN_PROGRESS_STATUSES or (
                    document.celery_task_id is not None
                    and document.status not in END_STATUSES
                )
    except Exception:
        poll = False

    poll_url = request.path if poll else None
    if poll and (search or page > 1):
        params = {}
        if search:
            params["search"] = search
        if page > 1:
            params["page"] = page
        poll_url = f"{poll_url}?{urlencode(params)}"

    # Apply DB-level sorting and pagination
    sort_key = _get_sort_pref(request, data_source_id)
    ordered_qs = _apply_queryset_sort(documents, sort_key)

    # If we have a search filter, we need to count the filtered results
    # Otherwise, use the cached total_docs
    if search:
        total_document_count = ordered_qs.count()
    else:
        total_document_count = total_docs

    total_pages = (
        total_document_count + DOCUMENTS_PER_PAGE - 1
    ) // DOCUMENTS_PER_PAGE or 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * DOCUMENTS_PER_PAGE
    end = start + DOCUMENTS_PER_PAGE
    documents = list(ordered_qs[start:end])

    # Fetch the latest document state from database to ensure we have current status
    show_data_source_status = document is None

    # Compute totals during polling so header can update live (unfiltered)
    data_source_status_summary = None
    data_source_status_counts = {}
    try:
        (
            total_cost,
            total_chunks,
            data_source_status_counts,
            data_source_status_summary,
        ) = _get_data_source_metrics(data_source_id)
        failed_count = data_source_status_counts.get("ERROR", 0)
        blocked_count = data_source_status_counts.get("BLOCKED", 0)
        paused_count = data_source_status_counts.get("PAUSED", 0)
    except Exception:
        total_cost = None
        total_chunks = None
        failed_count = None
        blocked_count = None
        paused_count = None
        data_source_status_counts = {}
        data_source_status_summary = None
    return render(
        request,
        "librarian/components/poll_update.html",
        {
            "documents": documents,
            "poll_url": poll_url,
            "poll_disabled_reason": poll_disabled_reason,
            "selected_document": document,
            "selected_data_source": data_source,
            "selected_library": data_source.library,
            "total_cost": total_cost,
            "total_chunks": total_chunks,
            "failed_count": failed_count or 0,
            "blocked_count": blocked_count or 0,
            "paused_count": paused_count or 0,
            "data_source_status_summary": data_source_status_summary,
            "data_source_status_counts": data_source_status_counts,
            "show_data_source_status": show_data_source_status,
            "current_sort": _get_sort_pref(request, data_source_id),
            "search": search,
            "page": page,
            "total_pages": total_pages,
            "total_document_count": total_document_count,
        },
    )


def modal_library_list(request):
    return modal_view(request)


def modal_create_library(request):
    if request.method == "POST":
        is_public = "is_public" in request.POST
        if is_public and not request.user.has_perm("librarian.manage_public_libraries"):
            return HttpResponse(status=403)
    return modal_view(request, item_type="library")


@permission_required("librarian.view_library", objectgetter(Library, "library_id"))
def modal_view_library(request, library_id):
    if request.method == "POST":
        is_public = "is_public" in request.POST
        if is_public and not request.user.has_perm("librarian.manage_public_libraries"):
            return HttpResponse(status=403)
    return modal_view(request, item_type="library", item_id=library_id)


@permission_required("librarian.delete_library", objectgetter(Library, "library_id"))
def modal_delete_library(request, library_id):
    return modal_view(request, item_type="library", item_id=library_id)


# AC-20: Only authenticated and authorized users can interact with information sources
@permission_required("librarian.edit_library", objectgetter(Library, "library_id"))
def modal_create_data_source(request, library_id):
    return modal_view(request, item_type="data_source", parent_id=library_id)


# AC-20: Only authenticated and authorized users can interact with information sources
@permission_required(
    "librarian.view_data_source", objectgetter(DataSource, "data_source_id")
)
def modal_view_data_source(request, data_source_id):
    return modal_view(request, item_type="data_source", item_id=data_source_id)


@permission_required(
    "librarian.delete_data_source", objectgetter(DataSource, "data_source_id")
)
def modal_delete_data_source(request, data_source_id):
    return modal_view(request, item_type="data_source", item_id=data_source_id)


# AC-20: Only authenticated and authorized users can interact with information sources
@permission_required(
    "librarian.edit_data_source", objectgetter(DataSource, "data_source_id")
)
def modal_create_document(request, data_source_id):
    return modal_view(request, item_type="document", parent_id=data_source_id)


@permission_required("librarian.view_document", objectgetter(Document, "document_id"))
# AC-20: Only authenticated and authorized users can interact with information sources
def modal_view_document(request, document_id):
    return modal_view(request, item_type="document", item_id=document_id)


@permission_required("librarian.delete_document", objectgetter(Document, "document_id"))
def modal_delete_document(request, document_id):
    return modal_view(request, item_type="document", item_id=document_id)


# AC-21: Only authenticated and authorized users can manage library users
@permission_required(
    "librarian.manage_library_users", objectgetter(Library, "library_id")
)
def modal_manage_library_users(request, library_id):
    return modal_view(request, item_type="library_users", item_id=library_id)


@dataclass
class LibrarianTempObject:
    id: int = None
    name: str = ""
    temp: bool = True


def create_temp_object(item_type):
    """
    Helper for creating a temporary object for the modal
    """
    temp_names = {
        "document": _("Unsaved document"),
        "data_source": _("Unsaved folder"),
        "library": _("Unsaved library"),
    }
    return LibrarianTempObject(id=None, name=temp_names[item_type], temp=True)


@permission_required("librarian.edit_document", objectgetter(Document, "document_id"))
@budget_required
def document_start(request, document_id, pdf_method="default"):
    user_id = request.user.id
    active_cost_group = request.user.get_active_cost_group(request)
    cost_group_id = active_cost_group.id if active_cost_group else None
    bind_contextvars(feature="librarian", user_id=user_id, cost_group_id=cost_group_id)

    # Initiate celery task
    document = get_object_or_404(Document, id=document_id)
    refresh_from_url = request.GET.get("refresh_from_url", "false") == "true"
    document.process(pdf_method=pdf_method, refresh_from_url=refresh_from_url)
    return modal_view(request, item_type="document", item_id=document_id)


@permission_required("librarian.edit_document", objectgetter(Document, "document_id"))
@budget_required
def document_start_embedding(request, document_id):
    user_id = request.user.id
    active_cost_group = request.user.get_active_cost_group(request)
    cost_group_id = active_cost_group.id if active_cost_group else None
    bind_contextvars(feature="librarian", user_id=user_id, cost_group_id=cost_group_id)

    document = get_object_or_404(Document, id=document_id)
    document.start_manual_embedding()
    return modal_view(request, item_type="document", item_id=document_id)


@permission_required("librarian.edit_document", objectgetter(Document, "document_id"))
def document_stop(request, document_id):
    # Stop celery task
    document = get_object_or_404(Document, id=document_id)
    document.stop()
    return modal_view(request, item_type="document", item_id=document_id)


@permission_required(
    "librarian.edit_data_source", objectgetter(DataSource, "data_source_id")
)
def data_source_stop(request, data_source_id):
    # Stop all celery tasks for documents within this data source
    data_source = get_object_or_404(DataSource, id=data_source_id)
    # Use only() to fetch only the fields needed for this operation
    for document in data_source.documents.only(
        "id", "uuid_hex", "status", "data_source_id"
    ).all():
        if document.status not in END_STATUSES:
            document.stop()
    return modal_view(request, item_type="data_source", item_id=data_source_id)


@permission_required(
    "librarian.edit_data_source", objectgetter(DataSource, "data_source_id")
)
@budget_required
def data_source_start(request, data_source_id, pdf_method="default", scope="all"):
    # Start all celery tasks for documents within this data source
    user_id = request.user.id
    active_cost_group = request.user.get_active_cost_group(request)
    cost_group_id = active_cost_group.id if active_cost_group else None
    bind_contextvars(feature="librarian", user_id=user_id, cost_group_id=cost_group_id)
    data_source = get_object_or_404(DataSource, id=data_source_id)
    # Use only() to fetch only the fields needed for this operation
    documents_qs = data_source.documents.only(
        "id", "uuid_hex", "status", "data_source_id"
    ).all()
    if scope == "all":
        for document in documents_qs:
            if document.status not in END_STATUSES:
                document.stop()
            document.process(pdf_method=pdf_method)
    elif scope == "incomplete":
        for document in documents_qs:
            if document.status not in END_STATUSES:
                document.stop()
            if document.status not in ["SUCCESS", "PAUSED"]:
                document.process(pdf_method=pdf_method)
    else:
        raise ValueError(f"Invalid scope: {scope}")
    return modal_view(request, item_type="data_source", item_id=data_source_id)


@permission_required(
    "librarian.edit_data_source", objectgetter(DataSource, "data_source_id")
)
@budget_required
def data_source_embed_large(request, data_source_id):
    user_id = request.user.id
    active_cost_group = request.user.get_active_cost_group(request)
    cost_group_id = active_cost_group.id if active_cost_group else None
    bind_contextvars(feature="librarian", user_id=user_id, cost_group_id=cost_group_id)

    data_source = get_object_or_404(DataSource, id=data_source_id)
    for document in data_source.documents.filter(status="PAUSED"):
        document.start_manual_embedding()
    return modal_view(request, item_type="data_source", item_id=data_source_id)


@permission_required(
    "librarian.edit_data_source", objectgetter(DataSource, "data_source_id")
)
@budget_required
def upload(request, data_source_id):
    """
    Handles the form submission after JS upload using UploadForm and django-file-form
    """
    user_id = request.user.id
    active_cost_group = request.user.get_active_cost_group(request)
    cost_group_id = active_cost_group.id if active_cost_group else None
    bind_contextvars(feature="librarian", user_id=user_id, cost_group_id=cost_group_id)
    data_source = get_object_or_404(
        DataSource.objects.select_related("library"), id=data_source_id
    )
    if _is_chat_uploads_folder(data_source):
        messages.warning(
            request,
            _(
                "Upload files directly in the chat. "
                "Chat folders are view-only in Manage libraries."
            ),
        )
        request.method = "GET"
        return modal_view(request, item_type="data_source", item_id=data_source_id)

    existing_document_count = 0
    form = UploadForm(request.POST, request.FILES, prefix="librarian")
    if form.is_valid():
        saved_files = form.save()
        for saved_file in saved_files:
            file_obj = saved_file["saved_file"]
            filename = saved_file["filename"]
            # Check if identical document already exists in the DataSource
            existing_document = Document.objects.filter(
                data_source_id=data_source_id,
                filename=filename,
                saved_file=file_obj,
            ).first()
            if existing_document:
                existing_document_count += 1
                if existing_document.status in ["ERROR", "PENDING"]:
                    existing_document.process()
                continue
            document = Document.objects.create(
                data_source_id=data_source_id, saved_file=file_obj, filename=filename
            )
            document.process()
    else:
        logger.error("Error uploading files:", errors=form.errors)
        messages.error(request, _("There was an error uploading your files."))

    # Update the modal with the new documents
    request.method = "GET"
    if existing_document_count > 0:
        messages.warning(
            request,
            _("%(count)d identical document(s) already exist in the library. ")
            % {"count": existing_document_count},
        )
    return modal_view(request, item_type="data_source", item_id=data_source_id)


@permission_required(
    "librarian.edit_data_source", objectgetter(DataSource, "data_source_id")
)
@budget_required
def direct_upload(request, data_source_id):
    """
    TODO: Remove this! It's currently only used in tests.
    Handles POST request for (multiple) document upload
    <input type="file" name="file" id="document-file-input" multiple>
    """
    user_id = request.user.id
    active_cost_group = request.user.get_active_cost_group(request)
    cost_group_id = active_cost_group.id if active_cost_group else None
    bind_contextvars(feature="librarian", user_id=user_id, cost_group_id=cost_group_id)
    data_source = get_object_or_404(
        DataSource.objects.select_related("library"), id=data_source_id
    )
    if _is_chat_uploads_folder(data_source):
        messages.warning(
            request,
            _(
                "Upload files directly in the chat. "
                "Chat folders are view-only in Manage libraries."
            ),
        )
        request.method = "GET"
        return modal_view(request, item_type="data_source", item_id=data_source_id)

    existing_document_count = 0

    for file in request.FILES.getlist("file"):
        # Check if the file is already stored on the server
        file_hash = generate_hash(file)
        # Further check that the file is on disk
        file_obj = SavedFile.objects.filter(sha256_hash=file_hash).first()
        file_exists = file_obj is not None
        is_good_file = file_exists and os.path.exists(file_obj.file.path)
        if is_good_file:
            logger.info(
                f"Found existing SavedFile for {file.name}", saved_file_id=file_obj.id
            )
            # Check if identical document already exists in the DataSource
            existing_document = Document.objects.filter(
                data_source_id=data_source_id,
                filename=file.name,
                saved_file__sha256_hash=file_hash,
            ).first()
            # Skip if filename and hash are the same, but reprocess if ERROR status
            if existing_document:
                existing_document_count += 1
                if existing_document.status in ["ERROR", "PENDING"]:
                    existing_document.process()
                continue
        else:
            if not file_exists:
                file_obj = SavedFile.objects.create(
                    content_type=sanitize_content_type(file.content_type)
                )
            file_obj.file.save(file.name, file)
            file_obj.generate_hash()

        document = Document.objects.create(
            data_source_id=data_source_id, saved_file=file_obj, filename=file.name
        )
        document.process()
    # Update the modal with the new documents
    request.method = "GET"
    if existing_document_count > 0:
        messages.warning(
            request,
            _("%(count)d identical document(s) already exist in the library. ")
            % {"count": existing_document_count},
        )
    return modal_view(request, item_type="data_source", item_id=data_source_id)


def _build_document_file_response(saved_file, filename):
    file = saved_file.file
    file.open("rb")
    file.seek(0)
    return FileResponse(
        file,
        as_attachment=True,
        filename=filename,
        content_type=saved_file.content_type,
    )


@permission_required(
    "librarian.download_document", objectgetter(Document, "document_id")
)
def download_document(request, document_id):
    # AC-20: Provide an audit trail of interactions with external information sources
    logger.info("Downloading file for QA document", document_id=document_id)
    document = get_object_or_404(Document, pk=document_id)
    if not document.saved_file or not document.file_exists:
        messages.error(request, _("File missing from storage."))
        return HttpResponse(status=404)
    return _build_document_file_response(document.saved_file, document.filename)


@permission_required(
    "librarian.download_document", objectgetter(Document, "document_id")
)
def download_original_document(request, document_id):
    logger.info("Downloading original file for QA document", document_id=document_id)
    document = get_object_or_404(Document, pk=document_id)
    if not document.original_saved_file or not document.original_file_exists:
        messages.error(request, _("Original file missing from storage."))
        return HttpResponse(status=404)
    return _build_document_file_response(
        document.original_saved_file,
        document.original_filename or document.filename,
    )


@permission_required(
    "librarian.download_document", objectgetter(Document, "document_id")
)
def document_text(request, document_id):
    document = get_object_or_404(Document, pk=document_id)
    return HttpResponse(
        document.extracted_text, content_type="text/plain; charset=utf-8"
    )


def email_library_admins(request, library_id):
    otto_email = "otto@justice.gc.ca"
    library = get_object_or_404(Library, pk=library_id)
    library_admin_emails = list(
        LibraryUserRole.objects.filter(library=library, role="admin").values_list(
            "user__email", flat=True
        )
    )
    to = library_admin_emails or otto_email
    cc = otto_email if library_admin_emails else ""
    subject = f"Otto Q&A library: {library.name_en} | Bibliothèque de questions et réponses Otto: {library.name_fr}"
    body = (
        "Le message français suit l'anglais.\n"
        "---\n"
        "You are receiving this email because you are an administrator for the following Otto Q&A library:\n"
        f'"{library.name_en}"\n\n'
        "Action required:\n<<ADD REQUIRED ACTION HERE>>\n\n"
        "Please log into Otto, and within the AI Assistant Q&A sidebar, click Edit Libraries to manage the library.\n"
        "If you have any questions or concerns, please contact the Otto team and the requester by replying-all to this email.\n"
        "---\n\n"
        "Vous recevez ce courriel parce que vous êtes un administrateur de la bibliothèque de questions et réponses Otto suivante:\n"
        f"{library.name_fr}\n\n"
        "Action requise: <<AJOUTEZ L'ACTION REQUISE ICI>>\n\n"
        "Veuillez vous connecter à Otto et, dans la barre latérale de l'assistant Q&R, cliquez sur Modifier les bibliothèques pour gérer la bibliothèque.\n"
        "Si vous avez des questions ou des préoccupations, veuillez contacter l'équipe Otto et le demandeur en répondant à tous à cet e-mail."
    )
    # URL encode the subject and message

    return HttpResponse(
        f"<a href='{generate_mailto(to, cc, subject, body)}'>mailto link</a>"
    )


@permission_required(
    "librarian.view_data_source", objectgetter(DataSource, "data_source_id")
)
def sort_docs(request, data_source_id, sort_by):
    # Default sort direction for each field
    default_mapping = {
        "date": "date_desc",
        "filename": "filename_asc",
        "filetype": "filetype_asc",
        "chunks": "chunks_desc",
        "status": "status_asc",
    }

    # Get current sort preference
    current_sort = _get_sort_pref(request, data_source_id)
    default_key = default_mapping.get(sort_by)

    # If clicking the same sort, toggle direction
    if default_key and current_sort.startswith(sort_by):
        # Toggle between asc and desc
        if current_sort.endswith("_asc"):
            key = sort_by + "_desc"
        else:
            key = sort_by + "_asc"
    else:
        # Use default direction for this field
        key = default_key

    if key:
        _set_sort_pref(request, data_source_id, key)

    # If there's an active search term, reuse search_docs so sort+search work together
    query = (request.GET.get("search", "") or "").strip()
    if query:
        return search_docs(request, data_source_id)

    return modal_view(request, item_type="data_source", item_id=data_source_id)


@permission_required(
    "librarian.view_data_source", objectgetter(DataSource, "data_source_id")
)
def search_docs(request, data_source_id):
    selected_data_source = get_object_or_404(DataSource, id=data_source_id)

    # basic query
    query = (request.GET.get("search", "") or "").strip()

    # base queryset from the selected data source (avoid loading extracted_text)
    documents_qs = selected_data_source.documents.defer("extracted_text").all()
    base_qs = documents_qs  # keep an unfiltered reference for totals
    if query:
        # filename or manual_title always match; extracted_title only if manual_title is empty
        documents_qs = documents_qs.filter(
            Q(filename__icontains=query)
            | Q(manual_title__icontains=query)
            | (
                Q(extracted_title__icontains=query)
                & (Q(manual_title__isnull=True) | Q(manual_title=""))
            )
        )

    # Apply DB-level sorting
    sort_key = _get_sort_pref(request, data_source_id)
    documents_qs = _apply_queryset_sort(documents_qs, sort_key)

    # Pagination at DB level
    page = int(request.GET.get("page", 1))
    total_document_count = documents_qs.count()
    total_pages = (
        total_document_count + DOCUMENTS_PER_PAGE - 1
    ) // DOCUMENTS_PER_PAGE or 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * DOCUMENTS_PER_PAGE
    end = start + DOCUMENTS_PER_PAGE
    documents = list(documents_qs[start:end])

    selected_library = selected_data_source.library
    data_sources = selected_library.folders
    can_edit_data_source = request.user.has_perm(
        "librarian.edit_data_source", selected_data_source
    )

    # Compute totals from all documents in the data source (not filtered by search)
    try:
        total_usd = base_qs.aggregate(total=Sum("usd_cost")).get("total") or 0
        if not total_usd or float(total_usd) == 0.0:
            total_cost = "$0.00"
        else:
            total_cost = display_cad_cost(total_usd)
        total_chunks = base_qs.aggregate(total=Sum("num_chunks")).get("total") or 0
        failed_count = base_qs.filter(status="ERROR").count()
        blocked_count = base_qs.filter(status="BLOCKED").count()
        paused_count = base_qs.filter(status="PAUSED").count()
    except Exception:
        total_cost = None
        total_chunks = None
        failed_count = 0
        blocked_count = 0
        paused_count = 0

    # Check if polling should be disabled for large folders
    poll_disabled_reason = None
    try:
        total_docs = base_qs.count()
        if total_docs > MAX_DOCUMENTS_FOR_POLLING:
            poll_disabled_reason = "folder_too_large"
    except Exception:
        pass

    return render(
        request,
        "librarian/components/document_list.html",
        {
            "selected_data_source": selected_data_source,
            "selected_library": selected_library,
            "data_sources": data_sources,
            "documents": documents,
            "can_edit_data_source": can_edit_data_source,
            "search": query,
            "current_sort": _get_sort_pref(request, data_source_id),
            "total_cost": total_cost,
            "total_chunks": total_chunks,
            "failed_count": failed_count,
            "blocked_count": blocked_count,
            "paused_count": paused_count,
            "poll_disabled_reason": poll_disabled_reason,
            "page": page,
            "total_pages": total_pages,
            "total_document_count": total_document_count,
        },
    )


@permission_required("librarian.view_library", objectgetter(Library, "library_id"))
def refresh_hnsw_status(request, library_id):
    """Refresh the HNSW status for a library."""
    library = get_object_or_404(Library, id=library_id)
    return render(
        request,
        "librarian/components/hnsw_status.html",
        {
            "library": library,
            "swap": True,
        },
    )
