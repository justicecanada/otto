import os

from django.core.files.base import ContentFile

import pytest
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters

from chat.llm import OttoLLM
from chat.models import Chat, ChatFile, Message
from librarian.models import DataSource, Document, Library, SavedFile
from librarian.tasks import process_document_helper

this_dir = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.django_db
def test_message_pre_delete_removes_documents(all_apps_user):
    """
    Test that deleting a message in Q&A mode also deletes the corresponding Document objects
    and removes them from the vector store.
    """
    user = all_apps_user()

    # Create a chat with Q&A mode
    chat = Chat.objects.create(user=user)
    chat.options.mode = "qa"
    chat.options.save()

    # Create a message in Q&A mode
    message = Message.objects.create(
        chat=chat, text="What is this document about?", mode="qa"
    )

    # Create a saved file (simulating file upload)
    with open(os.path.join(this_dir, "../librarian/test_files/example.pdf"), "rb") as f:
        pdf_content = f.read()
        saved_file = SavedFile.objects.create(
            file=ContentFile(pdf_content, name="test_document.pdf"),
            content_type="application/pdf",
        )

    # Create a ChatFile attached to the message
    chat_file = ChatFile.objects.create(
        message=message, filename="test_document.pdf", saved_file=saved_file
    )

    # Get the data source that was automatically created for the chat
    data_source = DataSource.objects.get(chat=chat)

    # Create a Document object linked to the saved file
    document = Document.objects.create(
        data_source=data_source, saved_file=saved_file, filename="test_document.pdf"
    )

    # Process the document to add it to the vector store
    llm = OttoLLM(mock_embedding=True)
    process_document_helper(document, llm)

    # Verify the document was processed and exists in vector store
    document.refresh_from_db()
    assert document.status == "SUCCESS"
    assert document.num_chunks > 0

    # Query the vector store to confirm the document exists
    filters = MetadataFilters(
        filters=[
            MetadataFilter(
                key="node_type",
                value="document",
                operator="!=",
            ),
            MetadataFilter(
                key="doc_id",
                value=[document.uuid_hex],
                operator="in",
            ),
        ]
    )
    retriever = llm.get_retriever(user.personal_library.uuid_hex, filters)
    nodes_before_delete = retriever.retrieve("What is this about?")
    assert (
        len(nodes_before_delete) > 0
    ), "Document should exist in vector store before deletion"

    # Store IDs for verification after deletion
    document_id = document.id
    saved_file_id = saved_file.id
    chat_file_id = chat_file.id

    # Delete the message - this should trigger the pre_delete signal
    message.delete()

    # Verify the ChatFile still exists (it's only deleted when the message is deleted)
    # but the Document should be deleted due to the signal handler
    assert not Document.objects.filter(
        id=document_id
    ).exists(), "Document should be deleted"

    # The ChatFile should also be deleted since it's CASCADE related to Message
    assert not ChatFile.objects.filter(
        id=chat_file_id
    ).exists(), "ChatFile should be deleted"

    # The SavedFile might still exist if referenced elsewhere, but in this case it should be deleted
    # when the ChatFile is deleted (due to the post_delete signal on ChatFile)
    assert not SavedFile.objects.filter(
        id=saved_file_id
    ).exists(), "SavedFile should be deleted"

    # Most importantly, verify the document no longer exists in the vector store
    nodes_after_delete = retriever.retrieve("What is this about?")
    # Filter out any nodes that might match the deleted document
    matching_nodes = [
        node
        for node in nodes_after_delete
        if node.metadata.get("doc_id") == document.uuid_hex
    ]
    assert (
        len(matching_nodes) == 0
    ), "Document should not exist in vector store after deletion"


@pytest.mark.django_db
def test_message_pre_delete_non_qa_mode_no_deletion(all_apps_user):
    """
    Test that deleting a message NOT in Q&A mode does not delete Document objects.
    """
    user = all_apps_user()

    # Create a chat with regular chat mode (not Q&A)
    chat = Chat.objects.create(user=user)
    chat.options.mode = "chat"  # Not Q&A mode
    chat.options.save()

    # Create a message in regular chat mode
    message = Message.objects.create(
        chat=chat,
        text="Hello, this is a regular chat message",
        mode="chat",  # Not Q&A mode
    )

    # Create a saved file and ChatFile
    with open(os.path.join(this_dir, "../librarian/test_files/example.pdf"), "rb") as f:
        pdf_content = f.read()
        saved_file = SavedFile.objects.create(
            file=ContentFile(pdf_content, name="test_document.pdf"),
            content_type="application/pdf",
        )

    chat_file = ChatFile.objects.create(
        message=message, filename="test_document.pdf", saved_file=saved_file
    )

    # Get the data source that was automatically created for the chat
    data_source = DataSource.objects.get(chat=chat)

    document = Document.objects.create(
        data_source=data_source, saved_file=saved_file, filename="test_document.pdf"
    )

    # Store ID for verification
    document_id = document.id

    # Delete the message - this should NOT trigger document deletion since mode != "qa"
    message.delete()

    # Verify the Document still exists (should not be deleted for non-Q&A messages)
    assert Document.objects.filter(
        id=document_id
    ).exists(), "Document should NOT be deleted for non-Q&A messages"


@pytest.mark.django_db
def test_message_pre_delete_multiple_documents(all_apps_user):
    """
    Test that deleting a Q&A message with multiple attached files deletes all corresponding Documents.
    """
    user = all_apps_user()

    # Create a chat with Q&A mode
    chat = Chat.objects.create(user=user)
    chat.options.mode = "qa"
    chat.options.save()

    # Create a message in Q&A mode
    message = Message.objects.create(
        chat=chat, text="What do these documents say?", mode="qa"
    )

    # Get the data source that was automatically created for the chat
    data_source = DataSource.objects.get(chat=chat)

    # Create multiple files and documents
    document_ids = []

    for i in range(2):
        # Create saved file
        with open(
            os.path.join(this_dir, "../librarian/test_files/example.pdf"), "rb"
        ) as f:
            pdf_content = f.read()
            saved_file = SavedFile.objects.create(
                file=ContentFile(pdf_content, name=f"test_document_{i}.pdf"),
                content_type="application/pdf",
            )

        # Create ChatFile
        ChatFile.objects.create(
            message=message, filename=f"test_document_{i}.pdf", saved_file=saved_file
        )

        # Create Document
        document = Document.objects.create(
            data_source=data_source,
            saved_file=saved_file,
            filename=f"test_document_{i}.pdf",
        )
        document_ids.append(document.id)

    # Verify both documents exist
    for doc_id in document_ids:
        assert Document.objects.filter(
            id=doc_id
        ).exists(), f"Document {doc_id} should exist before deletion"

    # Delete the message
    message.delete()

    # Verify both documents are deleted
    for doc_id in document_ids:
        assert not Document.objects.filter(
            id=doc_id
        ).exists(), f"Document {doc_id} should be deleted"


@pytest.mark.django_db
def test_message_pre_delete_no_saved_file(all_apps_user):
    """
    Test that the pre_delete handler doesn't crash when ChatFile has no saved_file.
    """
    user = all_apps_user()

    # Create a chat with Q&A mode
    chat = Chat.objects.create(user=user)
    chat.options.mode = "qa"
    chat.options.save()

    # Create a message in Q&A mode
    message = Message.objects.create(chat=chat, text="What is this about?", mode="qa")

    # Create a ChatFile with no saved_file (this can happen in edge cases)
    ChatFile.objects.create(
        message=message, filename="test_document.pdf", saved_file=None  # No saved file
    )

    # This should not crash
    try:
        message.delete()
        # If we get here, the test passed
        assert True
    except Exception as e:
        pytest.fail(
            f"Message deletion should not crash when ChatFile has no saved_file: {e}"
        )


@pytest.mark.django_db
def test_message_pre_delete_error_handling(all_apps_user, caplog):
    """
    Test that errors in pre_delete handler are properly logged and don't prevent deletion.
    """
    user = all_apps_user()

    # Create a chat with Q&A mode
    chat = Chat.objects.create(user=user)
    chat.options.mode = "qa"
    chat.options.save()

    # Create a message in Q&A mode
    message = Message.objects.create(chat=chat, text="What is this about?", mode="qa")

    # Create a saved file
    with open(os.path.join(this_dir, "../librarian/test_files/example.pdf"), "rb") as f:
        pdf_content = f.read()
        saved_file = SavedFile.objects.create(
            file=ContentFile(pdf_content, name="test_document.pdf"),
            content_type="application/pdf",
        )

    # Create ChatFile
    ChatFile.objects.create(
        message=message, filename="test_document.pdf", saved_file=saved_file
    )

    # Get the data source that was automatically created for the chat
    data_source = DataSource.objects.get(chat=chat)

    document = Document.objects.create(
        data_source=data_source, saved_file=saved_file, filename="test_document.pdf"
    )

    message_id = message.id

    # Mock Document.objects.filter to raise an exception
    from unittest.mock import MagicMock, patch

    def mock_filter(*args, **kwargs):
        mock_queryset = MagicMock()
        mock_queryset.first.side_effect = Exception("Simulated database error")
        return mock_queryset

    with patch("chat.models.Document.objects.filter", side_effect=mock_filter):
        # The message deletion should still work despite the error
        message.delete()

    # Verify message was deleted
    assert not Message.objects.filter(id=message_id).exists()
    # Check that error was logged
    assert "Message pre delete error" in caplog.text
