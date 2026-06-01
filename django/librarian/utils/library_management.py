from __future__ import annotations

from django.utils.translation import gettext as _

from librarian.models import DataSource, Library, LibraryUserRole

IMPORTED_URLS_FOLDER_NAME = _("Imported URLs")
CORPORATE_LIBRARY_ERROR = (
    "The URL retriever cannot add content to the Corporate Library"
)
MISSING_DESTINATION_ERROR = "Provide a library, folder, or enable add_to_chat_library."

# NOTE:
# The current url_retriever tool now always writes into the active chat uploads
# library for a simpler, more consistent tool contract. The broader helpers in
# this module are intentionally retained for future dedicated library-management
# or ingestion tools so we don't re-embed this logic in tool-specific code.


def get_or_create_imported_urls_data_source(library: Library) -> DataSource:
    """Return the default folder used for URL-ingested content."""
    data_source, __ = DataSource.objects.get_or_create(
        library=library,
        name=IMPORTED_URLS_FOLDER_NAME,
    )
    return data_source


def ensure_chat_data_source(chat) -> DataSource:
    """Ensure a chat has a backing data source and return it."""
    if hasattr(chat, "data_source") and chat.data_source:
        return chat.data_source

    from chat_next.models import create_chat_data_source

    return create_chat_data_source(chat.user, chat)


def create_library_with_default_folder(
    user,
    name: str,
    description: str,
    folder_name: str,
    is_public: bool,
) -> tuple[Library, DataSource]:
    """Create a library and default folder following the normal librarian flow.

    TODO: Implement library management agent tools.
    """
    library = Library.objects.create(
        name=name or _("Imported URLs"),
        description=description,
        created_by=user,
        is_public=is_public,
    )
    LibraryUserRole.objects.create(library=library, user=user, role="admin")
    library.reset()
    data_source = DataSource.objects.create(
        library=library,
        name=folder_name or _("Imported URLs"),
    )
    return library, data_source


def resolve_library_ingestion_destination(
    user,
    *,
    chat=None,
    target_library_id=None,
    target_data_source_id=None,
    add_to_chat_active: bool = False,
) -> dict:
    """Resolve where new ingested content should be stored.

    Returns a dict containing library/data_source identifiers and names, or an
    ``{"error": ...}`` payload when the destination is invalid.

    This is currently intended for future dedicated library-management tools.
    The chat_next URL retriever was intentionally narrowed to the current chat's
    uploads library to avoid off-pattern writes to arbitrary libraries/folders.
    """
    if target_data_source_id:
        try:
            data_source = DataSource.objects.select_related("library").get(
                id=target_data_source_id
            )
        except DataSource.DoesNotExist:
            return {"error": f"Folder {target_data_source_id} not found."}

        if data_source.library and data_source.library.is_default_library:
            return {"error": CORPORATE_LIBRARY_ERROR}
        if not user.has_perm("librarian.edit_data_source", data_source):
            return {
                "error": "You do not have permission to add documents to that folder."
            }

        return {
            "library_id": data_source.library_id,
            "library_name": str(data_source.library),
            "data_source_id": data_source.id,
            "data_source_name": data_source.name,
        }

    if target_library_id:
        try:
            library = Library.objects.get(id=target_library_id)
        except Library.DoesNotExist:
            return {"error": f"Library {target_library_id} not found."}

        if library.is_default_library:
            return {"error": CORPORATE_LIBRARY_ERROR}
        if not user.has_perm("librarian.edit_library", library):
            return {
                "error": "You do not have permission to add documents to that library."
            }

        data_source = get_or_create_imported_urls_data_source(library)
        return {
            "library_id": library.id,
            "library_name": str(library),
            "data_source_id": data_source.id,
            "data_source_name": data_source.name,
        }

    if add_to_chat_active:
        if not chat:
            return {
                "error": "Chat context is required to add documents to chat uploads."
            }

        data_source = ensure_chat_data_source(chat)
        library = data_source.library
        return {
            "library_id": library.id,
            "library_name": str(library),
            "data_source_id": data_source.id,
            "data_source_name": data_source.name,
            "chat_data_source_id": data_source.id,
        }

    return {"error": MISSING_DESTINATION_ERROR}
