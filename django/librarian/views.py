# views.py
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _

from rules.contrib.views import objectgetter
from structlog import get_logger

from otto.utils.decorators import permission_required

from .forms import (
    DataSourceDetailForm,
    DocumentDetailForm,
    LibraryDetailForm,
    LibraryUsersForm,
)
from .models import DataSource, Document, Library, SavedFile

logger = get_logger(__name__)


def get_editable_libraries(user):
    return [
        library
        for library in Library.objects.filter(chat=None)
        if user.has_perm("librarian.edit_library", library)
    ]


# TODO: Permission checking on the requested / posted item!
# Probably easiest to do by refactoring URLs per-item-type & action.
# e.g. /modal/library/<int:library_id>/create_data_source/ can use the following:
# @permission_required("librarian.edit_library", objectgetter(Library, "library_id"))
# e.g. /modal/library/<int:library_id>/delete/ can use the following:
# @permission_required("librarian.delete_library", objectgetter(Library, "library_id"))
# e.g. /modal/data_source/<int:data_source_id>/ can use the following:
# @permission_required("librarian.edit_data_source", objectgetter(DataSource, "data_source_id"))
# Each of these views will be very thin, actually just returning this view with different parameters.
def modal(request, item_type=None, item_id=None, parent_id=None):
    """
    This _beastly_ function handles almost all actions in the "Edit libraries" modal.

    This includes the initial view (no library selected), and the subsequent views for
    editing a library, data source, or document; creating each of the same; including
    GET, POST, and DELETE requests. It also handles library user management.

    The modal is updated with the new content after each request.
    When a data source is visible that contains in-progress documents, the modal will
    poll for updates until all documents are processed or stopped.
    """
    print(f"modal: {item_type}, {item_id}, {parent_id}")
    libraries = get_editable_libraries(request.user)
    selected_library = None
    data_sources = None
    selected_data_source = None
    documents = None
    selected_document = None
    form = None
    users_form = None
    show_document_status = False
    focus_el = None

    if item_type == "document":
        if request.method == "POST":
            document = Document.objects.get(id=item_id) if item_id else None
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
                selected_data_source = get_object_or_404(DataSource, id=parent_id)
        elif request.method == "DELETE":
            document = get_object_or_404(Document, id=item_id)
            document.delete()
            messages.success(request, _("Document deleted successfully."))
            selected_data_source = document.data_source
        else:
            if item_id:
                selected_document = get_object_or_404(Document, id=item_id)
                selected_data_source = selected_document.data_source
                show_document_status = True
            else:
                selected_data_source = get_object_or_404(DataSource, id=parent_id)
        documents = list(selected_data_source.documents.all())
        selected_library = selected_data_source.library
        data_sources = selected_library.data_sources.all()
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
            data_source = DataSource.objects.get(id=item_id) if item_id else None
            form = DataSourceDetailForm(
                request.POST,
                instance=data_source,
            )
            if form.is_valid():
                form.save()
                messages.success(
                    request,
                    (
                        _("Data source updated successfully.")
                        if item_id
                        else _("Data source created successfully.")
                    ),
                )
                selected_data_source = form.instance
                item_id = selected_data_source.id
                selected_library = selected_data_source.library
                documents = selected_data_source.documents.all()
            else:
                logger.error("Error updating data source:", errors=form.errors)
                selected_library = get_object_or_404(Library, id=parent_id)
        elif request.method == "DELETE":
            data_source = get_object_or_404(DataSource, id=item_id)
            data_source.delete()
            messages.success(request, _("Data source deleted successfully."))
            selected_library = data_source.library
            data_sources = selected_library.data_sources.all()
        else:
            if item_id:
                selected_data_source = get_object_or_404(DataSource, id=item_id)
                selected_library = selected_data_source.library
                documents = selected_data_source.documents.all()
            else:
                selected_library = get_object_or_404(Library, id=parent_id)
        data_sources = list(selected_library.data_sources.all())
        if not item_id and not request.method == "DELETE":
            new_data_source = create_temp_object("data_source")
            data_sources.insert(0, new_data_source)
            selected_data_source = new_data_source
            focus_el = "#id_name_en"
        if not request.method == "DELETE":
            form = form or DataSourceDetailForm(
                instance=selected_data_source if item_id else None,
                library_id=parent_id,
            )

    if item_type == "library":
        if request.method == "POST":
            library = Library.objects.get(id=item_id) if item_id else None
            form = LibraryDetailForm(request.POST, instance=library, user=request.user)
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
                libraries = get_editable_libraries(request.user)
                selected_library = form.instance
                # Refresh the form so "public" checkbox behaves properly
                form = LibraryDetailForm(instance=selected_library, user=request.user)
                item_id = selected_library.id
                data_sources = selected_library.data_sources.all()
                if request.user.has_perm(
                    "librarian.manage_library_users", selected_library
                ):
                    users_form = LibraryUsersForm(library=selected_library)
            else:
                logger.error("Error updating library:", errors=form.errors)
        elif request.method == "DELETE":
            library = get_object_or_404(Library, id=item_id)
            library.delete()
            messages.success(request, _("Library deleted successfully."))
            libraries = get_editable_libraries(request.user)
        if not request.method == "DELETE":
            if item_id:
                selected_library = get_object_or_404(Library, id=item_id)
                data_sources = selected_library.data_sources.all()
                if request.user.has_perm(
                    "librarian.manage_library_users", selected_library
                ):
                    users_form = LibraryUsersForm(library=selected_library)
            elif not selected_library:
                new_library = create_temp_object("library")
                libraries.insert(0, new_library)
                selected_library = new_library
                focus_el = "#id_name_en"
            form = form or LibraryDetailForm(
                instance=selected_library if item_id else None, user=request.user
            )

    if item_type == "library_users":
        if request.method == "POST":
            selected_library = get_object_or_404(Library, id=item_id)
            users_form = LibraryUsersForm(request.POST, library=selected_library)
            if users_form.is_valid():
                users_form.save()
                messages.success(request, _("Library users updated successfully."))
            else:
                logger.error("Error updating library users:", errors=users_form.errors)
            # The change may have resulted in the user losing access to manage library users
            if not request.user.has_perm(
                "librarian.manage_library_users", selected_library
            ):
                users_form = None
            data_sources = selected_library.data_sources.all()
            form = LibraryDetailForm(instance=selected_library, user=request.user)
        else:
            return HttpResponse(status=405)

    # Poll for updates when a data source is selected that has processing documents
    # (with status "INIT" or "PROCESSING")
    try:
        poll = selected_data_source.documents.filter(
            status__in=["INIT", "PROCESSING"]
        ).exists()
    except:
        poll = False
    # We have to construct the poll URL manually (instead of using request.path)
    # because some views, e.g. document_start, return this view from a different URL
    poll_url = (
        reverse(
            "librarian:modal_existing_item",
            kwargs={
                "item_type": "document" if selected_document else "data_source",
                "item_id": (
                    selected_document.id
                    if selected_document
                    else selected_data_source.id
                ),
            },
        )
        if poll
        else None
    )

    context = {
        "libraries": libraries,
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
    }
    return render(request, "librarian/modal_inner.html", context)


def create_temp_object(item_type):
    """
    Helper for creating a temporary object for the modal
    """
    temp_names = {
        "document": _("Unsaved document"),
        "data_source": _("Unsaved data source"),
        "library": _("Unsaved library"),
    }
    return {
        "id": None,
        "name": temp_names[item_type],
        "temp": True,
    }


@permission_required("librarian.edit_document", objectgetter(Document, "document_id"))
def document_start(request, document_id):
    # Initiate celery task
    document = get_object_or_404(Document, id=document_id)
    document.process()
    return modal(request, item_type="document", item_id=document_id)


@permission_required("librarian.edit_document", objectgetter(Document, "document_id"))
def document_stop(request, document_id):
    # Initiate celery task
    document = get_object_or_404(Document, id=document_id)
    document.stop()
    return modal(request, item_type="document", item_id=document_id)


@permission_required(
    "librarian.edit_data_source", objectgetter(DataSource, "data_source_id")
)
def upload(request, data_source_id):
    """
    Handles POST request for (multiple) document upload
    <input type="file" name="file" id="document-file-input" multiple>
    """
    print(f"upload: {data_source_id}")
    print(request.FILES)
    for file in request.FILES.getlist("file"):
        file_obj = SavedFile.objects.create(content_type=file.content_type)
        file_obj.file.save(file.name, file)
        document = Document.objects.create(
            data_source_id=data_source_id, file=file_obj, filename=file.name
        )
        document.process()
    # Update the modal with the new documents
    request.method = "GET"
    return modal(request, item_type="data_source", item_id=data_source_id)
