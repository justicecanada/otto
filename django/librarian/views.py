# views.py
from dataclasses import dataclass

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _

from data_fetcher.util import clear_request_caches
from rules.contrib.views import objectgetter
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from librarian.utils.process_engine import generate_hash
from otto.utils.common import generate_mailto
from otto.utils.decorators import budget_required, permission_required

from .forms import (
    DataSourceDetailForm,
    DocumentDetailForm,
    LibraryDetailForm,
    LibraryUsersForm,
)
from .models import DataSource, Document, Library, LibraryUserRole, SavedFile

logger = get_logger(__name__)
IN_PROGRESS_STATUSES = ["PENDING", "INIT", "PROCESSING"]


def get_editable_libraries(user):
    return [
        library
        for library in Library.objects.all()
        if user.has_perm("librarian.edit_library", library)
    ]


# AC-20: Implements role-based access control for interacting with data sources
def modal_view(request, item_type=None, item_id=None, parent_id=None):
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
    bind_contextvars(feature="librarian")

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
    has_error = False

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
                has_error = True
                selected_data_source = (
                    DataSource.objects.filter(id=parent_id).first()
                    or form.instance.data_source
                )
        elif request.method == "DELETE":
            if item_id == 1:
                return HttpResponse(status=400)
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
        documents = list(selected_data_source.documents.defer("extracted_text").all())
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
            data_source = DataSource.objects.get(id=item_id) if item_id else None
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
            data_source = get_object_or_404(DataSource, id=item_id)
            data_source.delete()
            messages.success(request, _("Folder deleted successfully."))
            selected_library = data_source.library
            data_sources = selected_library.folders
        else:
            if item_id:
                selected_data_source = get_object_or_404(DataSource, id=item_id)
                selected_library = selected_data_source.library
                documents = selected_data_source.documents.defer("extracted_text").all()
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
        if request.method == "POST":
            library = Library.objects.get(id=item_id) if item_id else None
            # Access library to update accessed_at field in order to reset the 30 days for deletion of unused libraries
            # This is not implemented using signals due to risk of introducing recursion
            if item_id:
                library.access()
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
                clear_request_caches()
                libraries = get_editable_libraries(request.user)
                selected_library = form.instance
                # Refresh the form so "public" checkbox behaves properly
                form = LibraryDetailForm(instance=selected_library, user=request.user)
                item_id = selected_library.id
                data_sources = selected_library.data_sources.all().prefetch_related(
                    "security_label"
                )
                if request.user.has_perm(
                    "librarian.manage_library_users", selected_library
                ):
                    users_form = LibraryUsersForm(library=selected_library)
            else:
                logger.error("Error updating library:", errors=form.errors)
                has_error = True
        elif request.method == "DELETE":
            library = get_object_or_404(Library, id=item_id)
            library.delete()
            messages.success(request, _("Library deleted successfully."))
            libraries = get_editable_libraries(request.user)
        if not request.method == "DELETE":
            if item_id:
                selected_library = get_object_or_404(Library, id=item_id)
                data_sources = selected_library.folders
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
            # Access library to update accessed_at field in order to reset the 30 days for deletion of unused libraries
            selected_library.access()
            users_form = LibraryUsersForm(request.POST, library=selected_library)
            if users_form.is_valid():
                users_form.save()
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
    try:
        poll = selected_data_source.documents.filter(
            status__in=IN_PROGRESS_STATUSES
        ).exists()
    except:
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
    else:
        poll_url = None

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
        "poll_response": "poll" in request.GET,
        "has_error": has_error,
    }
    return render(request, "librarian/modal_inner.html", context)


@permission_required(
    "librarian.edit_data_source", objectgetter(DataSource, "data_source_id")
)
def poll_status(request, data_source_id, document_id=None):
    """
    Polling view for data source status updates
    Updates the document list in the modal with updated titles / status icons
    """
    documents = Document.objects.filter(data_source_id=data_source_id)
    poll = False
    try:
        poll = documents.filter(status__in=IN_PROGRESS_STATUSES).exists()
    except:
        poll = False
    poll_url = request.path if poll else None

    document = Document.objects.get(id=document_id) if document_id else None
    return render(
        request,
        "librarian/components/poll_update.html",
        {"documents": documents, "poll_url": poll_url, "selected_document": document},
    )


def modal_library_list(request):
    return modal_view(request)


def modal_create_library(request):
    if request.method == "POST":
        is_public = "is_public" in request.POST
        if is_public and not request.user.has_perm("librarian.manage_public_libraries"):
            return HttpResponse(status=403)
    return modal_view(request, item_type="library")


@permission_required("librarian.edit_library", objectgetter(Library, "library_id"))
def modal_edit_library(request, library_id):
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
    "librarian.edit_data_source", objectgetter(DataSource, "data_source_id")
)
def modal_edit_data_source(request, data_source_id):
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


# AC-20: Only authenticated and authorized users can interact with information sources
@permission_required("librarian.edit_document", objectgetter(Document, "document_id"))
def modal_edit_document(request, document_id):
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
    bind_contextvars(feature="librarian")

    # Initiate celery task
    document = get_object_or_404(Document, id=document_id)
    document.process(pdf_method=pdf_method)
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
    for document in data_source.documents.defer("extracted_text").all():
        if document.status in ["PENDING", "INIT", "PROCESSING"]:
            document.stop()
    return modal_view(request, item_type="data_source", item_id=data_source_id)


@permission_required(
    "librarian.edit_data_source", objectgetter(DataSource, "data_source_id")
)
@budget_required
def data_source_start(request, data_source_id, pdf_method="default", scope="all"):
    # Start all celery tasks for documents within this data source
    bind_contextvars(feature="librarian")
    data_source = get_object_or_404(DataSource, id=data_source_id)
    if scope == "all":
        for document in data_source.documents.defer("extracted_text").all():
            if document.status in ["PENDING", "INIT", "PROCESSING"]:
                document.stop()
            document.process(pdf_method=pdf_method)
    elif scope == "incomplete":
        for document in data_source.documents.defer("extracted_text").all():
            if document.status in ["PENDING", "INIT", "PROCESSING"]:
                document.stop()
            if document.status not in ["SUCCESS"]:
                document.process(pdf_method=pdf_method)
    else:
        raise ValueError(f"Invalid scope: {scope}")
    return modal_view(request, item_type="data_source", item_id=data_source_id)


@permission_required(
    "librarian.edit_data_source", objectgetter(DataSource, "data_source_id")
)
@budget_required
def upload(request, data_source_id):
    """
    Handles POST request for (multiple) document upload
    <input type="file" name="file" id="document-file-input" multiple>
    """
    bind_contextvars(feature="librarian")

    for file in request.FILES.getlist("file"):
        # Check if the file is already stored on the server
        file_hash = generate_hash(file.read())
        file_exists = SavedFile.objects.filter(sha256_hash=file_hash).exists()
        if file_exists:
            file_obj = SavedFile.objects.filter(sha256_hash=file_hash).first()
            logger.info(
                f"Found existing SavedFile for {file.name}", saved_file_id=file_obj.id
            )
            # Check if identical document already exists in the DataSource
            existing_document = Document.objects.filter(
                data_source_id=data_source_id,
                filename=file.name,
                file__sha256_hash=file_hash,
            ).first()
            # Skip if filename and hash are the same, and processing status is SUCCESS
            if existing_document:
                if existing_document.status != "SUCCESS":
                    existing_document.process()
                continue
        else:
            file_obj = SavedFile.objects.create(content_type=file.content_type)
            file_obj.file.save(file.name, file)
            file_obj.generate_hash()

        document = Document.objects.create(
            data_source_id=data_source_id, file=file_obj, filename=file.name
        )
        document.process()
    # Update the modal with the new documents
    request.method = "GET"
    return modal_view(request, item_type="data_source", item_id=data_source_id)


@permission_required(
    "librarian.download_document", objectgetter(Document, "document_id")
)
def download_document(request, document_id):
    # AC-20: Provide an audit trail of interactions with external information sources
    logger.info("Downloading file for QA document", document_id=document_id)
    document = get_object_or_404(Document, pk=document_id)
    file_obj = document.file
    file = file_obj.file
    # Download the file, don't display it
    response = HttpResponse(file, content_type=file_obj.content_type)
    response["Content-Disposition"] = f"attachment; filename={document.filename}"
    return response


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
        f"<a href='{generate_mailto(to,cc,subject,body)}'>mailto link</a>"
    )
