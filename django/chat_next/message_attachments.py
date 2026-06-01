from __future__ import annotations

from django.utils.translation import gettext as _

from chat_next.models import ChatFile


def get_document_attachment_filename(document) -> str:
    """Return the best display filename for a document-backed message attachment."""
    return (
        document.file_path
        or document.filename
        or document.manual_title
        or document.extracted_title
        or document.generated_title
        or document.url
        or _("URL document")
    )


def ensure_message_attachment_for_document(
    message, document, filename: str | None = None
):
    """Ensure a message has a ChatFile attachment pointing at the given document.

    Returns a tuple of ``(chat_file, created)``. If the document has no saved file yet,
    returns ``(None, False)``.
    """
    if not getattr(document, "saved_file_id", None):
        return None, False

    desired_filename = filename or get_document_attachment_filename(document)
    chat_file = ChatFile.objects.filter(message=message, document=document).first()
    if chat_file:
        update_fields = []
        if chat_file.saved_file_id != document.saved_file_id:
            chat_file.saved_file = document.saved_file
            update_fields.append("saved_file")
        if chat_file.filename != desired_filename:
            chat_file.filename = desired_filename
            update_fields.append("filename")
        if update_fields:
            chat_file.save(update_fields=update_fields)
        return chat_file, False

    return (
        ChatFile.objects.create(
            message=message,
            filename=desired_filename,
            saved_file=document.saved_file,
            document=document,
        ),
        True,
    )
