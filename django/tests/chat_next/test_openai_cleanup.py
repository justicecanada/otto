"""
Tests for chat_next OpenAI file and response cleanup functionality.

Tests the signal handlers and Celery tasks that clean up OpenAI resources
when chats or messages are deleted.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from chat_next.models import Chat, ChatFile, Message, Skill
from chat_next.tasks import (
    cleanup_dangling_openai_files,
    cleanup_dangling_openai_responses,
    cleanup_dangling_transcription_blobs,
    delete_openai_file,
    delete_openai_response,
)

from librarian.models import Document, SavedFile


@pytest.mark.django_db
class TestOpenAIFileDeletion:
    """Tests for OpenAI file deletion on ChatFile delete."""

    def test_delete_chat_file_triggers_openai_deletion(self, all_apps_user):
        """Deleting a ChatFile with openai_file_id should trigger async deletion via safe_delete."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")
        message = Message.objects.create(chat=chat, text="Test", is_bot=False)

        # Create a SavedFile with an openai_file_id
        saved_file = SavedFile.objects.create(openai_file_id="file-test123")
        chat_file = ChatFile.objects.create(
            message=message,
            filename="test.pdf",
            saved_file=saved_file,
        )

        with patch(
            "chat_next.tasks.delete_openai_file_async.delay"
        ) as mock_delete_task:
            chat_file.delete()
            # OpenAI file deletion is triggered by SavedFile.safe_delete()
            mock_delete_task.assert_called_once_with("file-test123")

    def test_delete_chat_file_without_openai_id_no_task(self, all_apps_user):
        """Deleting a ChatFile without openai_file_id should not trigger deletion."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")
        message = Message.objects.create(chat=chat, text="Test", is_bot=False)

        # Create a SavedFile without an openai_file_id
        saved_file = SavedFile.objects.create()
        chat_file = ChatFile.objects.create(
            message=message,
            filename="test.pdf",
            saved_file=saved_file,
        )

        with patch(
            "chat_next.tasks.delete_openai_file_async.delay"
        ) as mock_delete_task:
            chat_file.delete()
            mock_delete_task.assert_not_called()

    def test_shared_savedfile_not_deleted_when_other_references_exist(
        self, all_apps_user
    ):
        """SavedFile with openai_file_id should NOT be deleted if other ChatFiles reference it."""
        user = all_apps_user()
        chat1 = Chat.objects.create(user=user, title="Chat 1")
        chat2 = Chat.objects.create(user=user, title="Chat 2")
        message1 = Message.objects.create(chat=chat1, text="Test", is_bot=False)
        message2 = Message.objects.create(chat=chat2, text="Test", is_bot=False)

        # Create a shared SavedFile with an openai_file_id
        saved_file = SavedFile.objects.create(openai_file_id="file-shared123")
        chat_file1 = ChatFile.objects.create(
            message=message1,
            filename="test.pdf",
            saved_file=saved_file,
        )
        chat_file2 = ChatFile.objects.create(
            message=message2,
            filename="test.pdf",
            saved_file=saved_file,
        )

        with patch(
            "chat_next.tasks.delete_openai_file_async.delay"
        ) as mock_delete_task:
            # Delete only the first ChatFile
            chat_file1.delete()
            # OpenAI file should NOT be deleted because chat_file2 still references saved_file
            mock_delete_task.assert_not_called()
            # SavedFile should still exist
            assert SavedFile.objects.filter(pk=saved_file.pk).exists()

        with patch(
            "chat_next.tasks.delete_openai_file_async.delay"
        ) as mock_delete_task:
            # Now delete the second ChatFile
            chat_file2.delete()
            # NOW the OpenAI file should be deleted
            mock_delete_task.assert_called_once_with("file-shared123")
            # SavedFile should now be deleted
            assert not SavedFile.objects.filter(pk=saved_file.pk).exists()


@pytest.mark.django_db
class TestOpenAIResponseDeletion:
    """Tests for OpenAI response deletion on Message delete."""

    def test_delete_bot_message_triggers_response_deletion(self, all_apps_user):
        """Deleting a bot message with response_id should trigger async deletion."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")
        user_msg = Message.objects.create(chat=chat, text="Hello", is_bot=False)
        bot_msg = Message.objects.create(
            chat=chat,
            text="Hi there",
            is_bot=True,
            parent=user_msg,
            response_id="resp-test123",
        )

        with patch(
            "chat_next.tasks.delete_openai_responses_batch.delay"
        ) as mock_delete_task:
            bot_msg.delete()
            mock_delete_task.assert_called_once()
            call_args = mock_delete_task.call_args[0][0]
            assert "resp-test123" in call_args

    def test_delete_bot_message_deletes_subsequent_responses(self, all_apps_user):
        """Deleting a bot message should also delete subsequent messages' responses."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        # Create a conversation with multiple responses
        user_msg1 = Message.objects.create(chat=chat, text="Hello", is_bot=False)
        bot_msg1 = Message.objects.create(
            chat=chat,
            text="Hi",
            is_bot=True,
            parent=user_msg1,
            response_id="resp-1",
        )
        user_msg2 = Message.objects.create(chat=chat, text="How are you?", is_bot=False)
        bot_msg2 = Message.objects.create(
            chat=chat,
            text="I'm good",
            is_bot=True,
            parent=user_msg2,
            response_id="resp-2",
        )
        user_msg3 = Message.objects.create(chat=chat, text="Great", is_bot=False)
        bot_msg3 = Message.objects.create(
            chat=chat,
            text="Thanks!",
            is_bot=True,
            parent=user_msg3,
            response_id="resp-3",
        )

        # Deleting bot_msg1 should trigger deletion of resp-1, resp-2, and resp-3
        with patch(
            "chat_next.tasks.delete_openai_responses_batch.delay"
        ) as mock_delete_task:
            bot_msg1.delete()
            mock_delete_task.assert_called_once()
            call_args = mock_delete_task.call_args[0][0]
            assert "resp-1" in call_args
            assert "resp-2" in call_args
            assert "resp-3" in call_args

        # Verify subsequent messages had their response_id cleared
        bot_msg2.refresh_from_db()
        bot_msg3.refresh_from_db()
        assert bot_msg2.response_id == ""
        assert bot_msg3.response_id == ""

    def test_delete_bot_message_clears_subsequent_response_ids(self, all_apps_user):
        """Deleting a bot message should clear response_id on subsequent bot messages."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        # Create a conversation with multiple responses
        user_msg1 = Message.objects.create(chat=chat, text="Hello", is_bot=False)
        bot_msg1 = Message.objects.create(
            chat=chat,
            text="Hi",
            is_bot=True,
            parent=user_msg1,
            response_id="resp-1",
        )
        user_msg2 = Message.objects.create(chat=chat, text="How are you?", is_bot=False)
        bot_msg2 = Message.objects.create(
            chat=chat,
            text="I'm good",
            is_bot=True,
            parent=user_msg2,
            response_id="resp-2",
        )
        user_msg3 = Message.objects.create(chat=chat, text="Great", is_bot=False)
        bot_msg3 = Message.objects.create(
            chat=chat,
            text="Thanks!",
            is_bot=True,
            parent=user_msg3,
            response_id="resp-3",
        )

        # Deleting bot_msg2 should clear response_id on bot_msg3
        with patch("chat_next.tasks.delete_openai_responses_batch.delay"):
            bot_msg2.delete()

        # bot_msg1 should still have its response_id (it's before the deleted message)
        bot_msg1.refresh_from_db()
        assert bot_msg1.response_id == "resp-1"

        # bot_msg3 should have its response_id cleared
        bot_msg3.refresh_from_db()
        assert bot_msg3.response_id == ""

    def test_delete_user_message_clears_subsequent_response_ids(self, all_apps_user):
        """Deleting a user message should also clear response_id on subsequent bot messages."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        # Create a conversation
        user_msg1 = Message.objects.create(chat=chat, text="Hello", is_bot=False)
        bot_msg1 = Message.objects.create(
            chat=chat,
            text="Hi",
            is_bot=True,
            parent=user_msg1,
            response_id="resp-1",
        )
        user_msg2 = Message.objects.create(chat=chat, text="Question", is_bot=False)
        bot_msg2 = Message.objects.create(
            chat=chat,
            text="Answer",
            is_bot=True,
            parent=user_msg2,
            response_id="resp-2",
        )

        # Deleting user_msg1 should clear response_id on bot_msg1 and bot_msg2
        # (since they are both at or after user_msg1's creation time)
        with patch(
            "chat_next.tasks.delete_openai_responses_batch.delay"
        ) as mock_delete_task:
            user_msg1.delete()
            # Should delete responses for subsequent bot messages
            mock_delete_task.assert_called_once()
            call_args = mock_delete_task.call_args[0][0]
            assert "resp-1" in call_args
            assert "resp-2" in call_args

        # Both bot messages should have response_id cleared
        bot_msg1.refresh_from_db()
        bot_msg2.refresh_from_db()
        assert bot_msg1.response_id == ""
        assert bot_msg2.response_id == ""

    def test_delete_user_message_without_subsequent_responses(self, all_apps_user):
        """Deleting a user message with no subsequent bot messages should not error."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")
        user_msg = Message.objects.create(chat=chat, text="Hello", is_bot=False)

        with patch(
            "chat_next.tasks.delete_openai_responses_batch.delay"
        ) as mock_delete_task:
            user_msg.delete()
            # No responses to delete
            mock_delete_task.assert_not_called()


@pytest.mark.django_db
class TestChatDeletionCleanup:
    """Tests for OpenAI cleanup when entire chat is deleted."""

    def test_delete_chat_triggers_response_deletion(self, all_apps_user):
        """Deleting a chat should trigger deletion of OpenAI responses."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        # Create messages with response_ids
        user_msg = Message.objects.create(chat=chat, text="Hello", is_bot=False)
        Message.objects.create(
            chat=chat,
            text="Hi",
            is_bot=True,
            parent=user_msg,
            response_id="resp-chat-test",
        )

        with patch(
            "chat_next.tasks.delete_openai_responses_batch.delay"
        ) as mock_responses:
            chat.delete()

            # Responses batch deletion should be called (may be called multiple times
            # because both chat_pre_delete and message_pre_delete fire)
            assert mock_responses.called
            # Check that the response ID is in at least one call
            all_response_ids = []
            for call in mock_responses.call_args_list:
                all_response_ids.extend(call[0][0])
            assert "resp-chat-test" in all_response_ids

    def test_delete_chat_triggers_file_deletion_via_safe_delete(self, all_apps_user):
        """Deleting a chat should trigger deletion of OpenAI files via SavedFile.safe_delete."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")

        # Create a message with a file
        user_msg = Message.objects.create(chat=chat, text="Hello", is_bot=False)

        # Create a file with openai_file_id
        saved_file = SavedFile.objects.create(openai_file_id="file-chat-test")
        ChatFile.objects.create(
            message=user_msg,
            filename="test.pdf",
            saved_file=saved_file,
        )

        with patch("chat_next.tasks.delete_openai_file_async.delay") as mock_files:
            chat.delete()

            # File deletion should be triggered by SavedFile.safe_delete()
            # when the ChatFile cascade deletes
            mock_files.assert_called_once_with("file-chat-test")

    def test_delete_chat_migrates_skill_referenced_document(self, all_apps_user):
        """Deleting a chat preserves document-referenced chat files in the user's skill library."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Referenced Chat")
        message = Message.objects.create(chat=chat, text="Hello", is_bot=False)

        saved_file = SavedFile.objects.create(openai_file_id="file-preserve-doc")
        source_doc = Document.objects.create(
            data_source=chat.data_source,
            saved_file=saved_file,
            filename="keep.txt",
            extracted_text="Important reference text",
            status="SUCCESS",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        ChatFile.objects.create(
            message=message,
            filename="keep.txt",
            saved_file=saved_file,
            document=source_doc,
        )

        skill = Skill.objects.create(
            display_name="Preserve doc skill",
            description="Uses a referenced chat file",
            body="body",
            owner=user,
            context_hints=[
                {"type": "document", "id": source_doc.id, "name": source_doc.filename}
            ],
        )

        with (
            patch("librarian.models.Document.process") as mock_process,
            patch("chat_next.tasks.delete_openai_file_async.delay") as mock_delete_file,
        ):
            chat.delete()

        skill.refresh_from_db()
        migrated_doc_id = int(skill.context_hints[0]["id"])
        migrated_doc = Document.objects.get(id=migrated_doc_id)

        assert migrated_doc.id != source_doc.id
        assert migrated_doc.saved_file_id == saved_file.id
        assert migrated_doc.data_source_id == skill.data_source.id
        assert migrated_doc.data_source.library.is_skill_library is True
        assert migrated_doc.extracted_text == "Important reference text"
        assert not Chat.objects.filter(id=chat.id).exists()
        assert not Document.objects.filter(id=source_doc.id).exists()
        assert SavedFile.objects.filter(id=saved_file.id).exists()
        mock_process.assert_not_called()
        mock_delete_file.assert_not_called()

    def test_delete_chat_migrates_entire_skill_referenced_folder(self, all_apps_user):
        """Deleting a chat preserves the whole folder when a skill references the chat folder."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Folder Chat")
        message = Message.objects.create(chat=chat, text="Hello", is_bot=False)

        saved_file_one = SavedFile.objects.create(openai_file_id="file-folder-1")
        saved_file_two = SavedFile.objects.create(openai_file_id="file-folder-2")

        doc_one = Document.objects.create(
            data_source=chat.data_source,
            saved_file=saved_file_one,
            filename="one.txt",
            extracted_text="Document one",
            status="SUCCESS",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        doc_two = Document.objects.create(
            data_source=chat.data_source,
            saved_file=saved_file_two,
            filename="two.txt",
            extracted_text="Document two",
            status="SUCCESS",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )

        ChatFile.objects.create(
            message=message,
            filename="one.txt",
            saved_file=saved_file_one,
            document=doc_one,
        )
        ChatFile.objects.create(
            message=message,
            filename="two.txt",
            saved_file=saved_file_two,
            document=doc_two,
        )

        skill = Skill.objects.create(
            display_name="Preserve folder skill",
            description="Uses a chat folder",
            body="body",
            owner=user,
            context_hints=[
                {
                    "type": "folder",
                    "id": chat.data_source.id,
                    "name": chat.data_source.name,
                },
                {"type": "document", "id": doc_one.id, "name": doc_one.filename},
            ],
        )

        with (
            patch("librarian.models.Document.process") as mock_process,
            patch("chat_next.tasks.delete_openai_file_async.delay") as mock_delete_file,
        ):
            chat.delete()

        skill.refresh_from_db()
        migrated_folder_id = int(skill.context_hints[0]["id"])
        migrated_doc_id = int(skill.context_hints[1]["id"])
        migrated_folder = doc_one.data_source.__class__.objects.get(
            id=migrated_folder_id
        )
        migrated_docs = list(
            Document.objects.filter(data_source=migrated_folder).order_by("filename")
        )

        assert migrated_folder.id == skill.data_source.id
        assert migrated_folder.library.is_skill_library is True
        assert [doc.filename for doc in migrated_docs] == ["one.txt", "two.txt"]
        assert {doc.saved_file_id for doc in migrated_docs} == {
            saved_file_one.id,
            saved_file_two.id,
        }
        assert migrated_doc_id in {doc.id for doc in migrated_docs}
        assert not Chat.objects.filter(id=chat.id).exists()
        assert SavedFile.objects.filter(id=saved_file_one.id).exists()
        assert SavedFile.objects.filter(id=saved_file_two.id).exists()
        mock_process.assert_not_called()
        mock_delete_file.assert_not_called()

    def test_delete_chat_rewrites_multiple_skills_referencing_same_document(
        self, all_apps_user
    ):
        """Deleting a chat rewrites every skill that references the same chat document."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Shared Reference Chat")
        message = Message.objects.create(chat=chat, text="Hello", is_bot=False)

        saved_file = SavedFile.objects.create(openai_file_id="file-shared-doc")
        source_doc = Document.objects.create(
            data_source=chat.data_source,
            saved_file=saved_file,
            filename="shared.txt",
            extracted_text="Shared reference text",
            status="SUCCESS",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        ChatFile.objects.create(
            message=message,
            filename="shared.txt",
            saved_file=saved_file,
            document=source_doc,
        )

        skill_one = Skill.objects.create(
            display_name="Preserve shared doc skill one",
            description="Uses a shared chat file",
            body="body",
            owner=user,
            context_hints=[
                {"type": "document", "id": source_doc.id, "name": source_doc.filename}
            ],
        )
        skill_two = Skill.objects.create(
            display_name="Preserve shared doc skill two",
            description="Also uses a shared chat file",
            body="body",
            owner=user,
            context_hints=[
                {"type": "document", "id": source_doc.id, "name": source_doc.filename}
            ],
        )

        with (
            patch("librarian.models.Document.process") as mock_process,
            patch("chat_next.tasks.delete_openai_file_async.delay") as mock_delete_file,
        ):
            chat.delete()

        skill_one.refresh_from_db()
        skill_two.refresh_from_db()
        migrated_doc_id_one = int(skill_one.context_hints[0]["id"])
        migrated_doc_id_two = int(skill_two.context_hints[0]["id"])

        assert migrated_doc_id_one != migrated_doc_id_two
        migrated_doc_one = Document.objects.get(id=migrated_doc_id_one)
        migrated_doc_two = Document.objects.get(id=migrated_doc_id_two)
        assert migrated_doc_one.saved_file_id == saved_file.id
        assert migrated_doc_two.saved_file_id == saved_file.id
        assert migrated_doc_one.data_source_id == skill_one.data_source.id
        assert migrated_doc_two.data_source_id == skill_two.data_source.id
        assert migrated_doc_one.data_source.library.is_skill_library is True
        assert migrated_doc_two.data_source.library.is_skill_library is True
        assert not Document.objects.filter(id=source_doc.id).exists()
        assert SavedFile.objects.filter(id=saved_file.id).exists()
        mock_process.assert_not_called()
        mock_delete_file.assert_not_called()

    def test_delete_chat_folder_reference_copies_into_each_skill_folder(
        self, all_apps_user
    ):
        """Folder-referenced chat files are copied into each referencing skill's folder."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Multi Skill Folder Chat")
        message = Message.objects.create(chat=chat, text="Hello", is_bot=False)

        saved_file_one = SavedFile.objects.create(openai_file_id="file-multi-folder-1")
        saved_file_two = SavedFile.objects.create(openai_file_id="file-multi-folder-2")

        doc_one = Document.objects.create(
            data_source=chat.data_source,
            saved_file=saved_file_one,
            filename="one.txt",
            extracted_text="Document one",
            status="SUCCESS",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        doc_two = Document.objects.create(
            data_source=chat.data_source,
            saved_file=saved_file_two,
            filename="two.txt",
            extracted_text="Document two",
            status="SUCCESS",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )

        ChatFile.objects.create(
            message=message,
            filename="one.txt",
            saved_file=saved_file_one,
            document=doc_one,
        )
        ChatFile.objects.create(
            message=message,
            filename="two.txt",
            saved_file=saved_file_two,
            document=doc_two,
        )

        skill_one = Skill.objects.create(
            display_name="Preserve folder skill one",
            description="Uses a chat folder",
            body="body",
            owner=user,
            context_hints=[
                {
                    "type": "folder",
                    "id": chat.data_source.id,
                    "name": chat.data_source.name,
                }
            ],
        )
        skill_two = Skill.objects.create(
            display_name="Preserve folder skill two",
            description="Also uses a chat folder",
            body="body",
            owner=user,
            context_hints=[
                {
                    "type": "folder",
                    "id": chat.data_source.id,
                    "name": chat.data_source.name,
                }
            ],
        )

        with (
            patch("librarian.models.Document.process") as mock_process,
            patch("chat_next.tasks.delete_openai_file_async.delay") as mock_delete_file,
        ):
            chat.delete()

        skill_one.refresh_from_db()
        skill_two.refresh_from_db()
        skill_one_docs = list(
            Document.objects.filter(data_source=skill_one.data_source).order_by(
                "filename"
            )
        )
        skill_two_docs = list(
            Document.objects.filter(data_source=skill_two.data_source).order_by(
                "filename"
            )
        )

        assert int(skill_one.context_hints[0]["id"]) == skill_one.data_source.id
        assert int(skill_two.context_hints[0]["id"]) == skill_two.data_source.id
        assert [doc.filename for doc in skill_one_docs] == ["one.txt", "two.txt"]
        assert [doc.filename for doc in skill_two_docs] == ["one.txt", "two.txt"]
        assert {doc.saved_file_id for doc in skill_one_docs} == {
            saved_file_one.id,
            saved_file_two.id,
        }
        assert {doc.saved_file_id for doc in skill_two_docs} == {
            saved_file_one.id,
            saved_file_two.id,
        }
        mock_process.assert_not_called()
        mock_delete_file.assert_not_called()

    def test_delete_chat_does_not_preserve_unreferenced_document(self, all_apps_user):
        """Deleting a chat only preserves explicitly referenced chat documents."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Selective Preserve Chat")
        message = Message.objects.create(chat=chat, text="Hello", is_bot=False)

        referenced_file = SavedFile.objects.create(openai_file_id="file-keep-me")
        unreferenced_file = SavedFile.objects.create(openai_file_id="file-delete-me")

        referenced_doc = Document.objects.create(
            data_source=chat.data_source,
            saved_file=referenced_file,
            filename="keep.txt",
            extracted_text="Keep this one",
            status="SUCCESS",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        unreferenced_doc = Document.objects.create(
            data_source=chat.data_source,
            saved_file=unreferenced_file,
            filename="delete.txt",
            extracted_text="Do not keep this one",
            status="SUCCESS",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )

        ChatFile.objects.create(
            message=message,
            filename="keep.txt",
            saved_file=referenced_file,
            document=referenced_doc,
        )
        ChatFile.objects.create(
            message=message,
            filename="delete.txt",
            saved_file=unreferenced_file,
            document=unreferenced_doc,
        )

        skill = Skill.objects.create(
            display_name="Preserve only referenced skill",
            description="Uses exactly one chat file",
            body="body",
            owner=user,
            context_hints=[
                {
                    "type": "document",
                    "id": referenced_doc.id,
                    "name": referenced_doc.filename,
                }
            ],
        )

        with (
            patch("librarian.models.Document.process") as mock_process,
            patch("chat_next.tasks.delete_openai_file_async.delay") as mock_delete_file,
        ):
            chat.delete()

        skill.refresh_from_db()
        migrated_doc = Document.objects.get(id=int(skill.context_hints[0]["id"]))

        assert migrated_doc.filename == "keep.txt"
        assert migrated_doc.saved_file_id == referenced_file.id
        assert migrated_doc.data_source_id == skill.data_source.id
        assert not SavedFile.objects.filter(id=unreferenced_file.id).exists()
        mock_process.assert_not_called()
        mock_delete_file.assert_called_once_with("file-delete-me")

    def test_delete_chat_migrates_folder_reference_by_name_and_library(
        self, all_apps_user
    ):
        """Folder references still migrate when the hint shape is matched by library/name."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Folder Name Match Chat")
        message = Message.objects.create(chat=chat, text="Hello", is_bot=False)

        saved_file = SavedFile.objects.create(openai_file_id="file-folder-name-match")
        doc = Document.objects.create(
            data_source=chat.data_source,
            saved_file=saved_file,
            filename="keep.txt",
            extracted_text="Document text",
            status="SUCCESS",
            provenance=Document.PROVENANCE_USER_UPLOAD,
        )
        ChatFile.objects.create(
            message=message,
            filename="keep.txt",
            saved_file=saved_file,
            document=doc,
        )

        skill = Skill.objects.create(
            display_name="Preserve folder by name skill",
            description="Uses a chat folder matched by library/name",
            body="body",
            owner=user,
            context_hints=[
                {
                    "type": "folder",
                    "id": 999999,
                    "name": chat.data_source.name,
                    "parent_library_id": chat.data_source.library_id,
                }
            ],
        )

        with (
            patch("librarian.models.Document.process") as mock_process,
            patch("chat_next.tasks.delete_openai_file_async.delay") as mock_delete_file,
        ):
            chat.delete()

        skill.refresh_from_db()
        migrated_folder_id = int(skill.context_hints[0]["id"])
        migrated_folder = doc.data_source.__class__.objects.get(id=migrated_folder_id)
        migrated_docs = list(Document.objects.filter(data_source=migrated_folder))

        assert migrated_folder.id == skill.data_source.id
        assert len(migrated_docs) == 1
        assert migrated_docs[0].filename == "keep.txt"
        assert migrated_docs[0].saved_file_id == saved_file.id
        mock_process.assert_not_called()
        mock_delete_file.assert_not_called()


class TestOpenAIAPIHelpers:
    """Tests for the OpenAI API helper functions."""

    def test_delete_openai_file_success(self):
        """delete_openai_file should return True on successful deletion."""
        with patch("chat_next.tasks.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_get_client.return_value = mock_client

            result = delete_openai_file("file-test123")

            assert result is True
            mock_client.files.delete.assert_called_once_with("file-test123")

    def test_delete_openai_file_not_found(self):
        """delete_openai_file should return True if file not found (already deleted)."""
        with patch("chat_next.tasks.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.files.delete.side_effect = Exception("File not found")
            mock_get_client.return_value = mock_client

            result = delete_openai_file("file-nonexistent")

            assert result is True

    def test_delete_openai_file_empty_id(self):
        """delete_openai_file should return False for empty file_id."""
        result = delete_openai_file("")
        assert result is False

        result = delete_openai_file(None)
        assert result is False

    def test_delete_openai_response_success(self):
        """delete_openai_response should return True on successful deletion."""
        with patch("chat_next.tasks.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_get_client.return_value = mock_client

            result = delete_openai_response("resp-test123")

            assert result is True
            mock_client.responses.delete.assert_called_once_with("resp-test123")

    def test_delete_openai_response_not_found(self):
        """delete_openai_response should return True if response not found."""
        with patch("chat_next.tasks.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.responses.delete.side_effect = Exception("Response not found")
            mock_get_client.return_value = mock_client

            result = delete_openai_response("resp-nonexistent")

            assert result is True

    def test_delete_openai_response_empty_id(self):
        """delete_openai_response should return False for empty response_id."""
        result = delete_openai_response("")
        assert result is False

        result = delete_openai_response(None)
        assert result is False


@pytest.mark.django_db
class TestCleanupTasks:
    """Tests for the nightly cleanup Celery tasks."""

    def test_cleanup_dangling_files_no_files(self):
        """cleanup_dangling_openai_files should handle empty file list."""
        with patch("chat_next.tasks.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.files.list.return_value = MagicMock(data=[])
            mock_get_client.return_value = mock_client

            result = cleanup_dangling_openai_files()

            assert result["deleted"] == 0
            assert result["total_openai"] == 0

    def test_cleanup_dangling_files_deletes_orphans(self):
        """cleanup_dangling_openai_files should delete files not in DB."""
        # Create a SavedFile with an openai_file_id
        saved_file = SavedFile.objects.create(openai_file_id="file-in-db")

        # Mock OpenAI returning files including one not in our DB
        mock_file_in_db = MagicMock()
        mock_file_in_db.id = "file-in-db"
        mock_file_orphan = MagicMock()
        mock_file_orphan.id = "file-orphan"

        with patch("chat_next.tasks.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.files.list.return_value = MagicMock(
                data=[mock_file_in_db, mock_file_orphan]
            )
            mock_get_client.return_value = mock_client

            result = cleanup_dangling_openai_files()

            # Should delete the orphan file
            mock_client.files.delete.assert_called_once_with("file-orphan")
            assert result["deleted"] == 1
            assert result["total_openai"] == 2
            assert result["dangling"] == 1

        # Cleanup
        saved_file.delete()

    def test_cleanup_dangling_responses_no_responses(self):
        """cleanup_dangling_openai_responses should handle empty response list."""
        with patch("chat_next.tasks.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.responses.list.return_value = MagicMock(data=[])
            mock_get_client.return_value = mock_client

            result = cleanup_dangling_openai_responses()

            assert result["deleted"] == 0
            assert result["total_openai"] == 0

    def test_cleanup_dangling_responses_deletes_orphans(self, all_apps_user):
        """cleanup_dangling_openai_responses should delete responses not in DB."""
        user = all_apps_user()
        chat = Chat.objects.create(user=user, title="Test")
        user_msg = Message.objects.create(chat=chat, text="Hello", is_bot=False)
        Message.objects.create(
            chat=chat,
            text="Hi",
            is_bot=True,
            parent=user_msg,
            response_id="resp-in-db",
        )

        # Mock OpenAI returning responses including one not in our DB
        mock_resp_in_db = MagicMock()
        mock_resp_in_db.id = "resp-in-db"
        mock_resp_orphan = MagicMock()
        mock_resp_orphan.id = "resp-orphan"

        with patch("chat_next.tasks.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.responses.list.return_value = MagicMock(
                data=[mock_resp_in_db, mock_resp_orphan]
            )
            mock_get_client.return_value = mock_client

            result = cleanup_dangling_openai_responses()

            # Should delete the orphan response
            mock_client.responses.delete.assert_called_once_with("resp-orphan")
            assert result["deleted"] == 1
            assert result["total_openai"] == 2
            assert result["dangling"] == 1


class TestCleanupTranscriptionBlobs:
    """Tests for the nightly transcription blob cleanup task."""

    def _make_blob(self, name, age_hours):
        """Helper: create a mock blob with last_modified set relative to now."""
        blob = MagicMock()
        blob.name = name
        blob.last_modified = datetime.now(tz=timezone.utc) - timedelta(hours=age_hours)
        return blob

    def test_cleanup_transcription_blobs_no_blobs(self, settings):
        """cleanup_dangling_transcription_blobs should handle empty blob list."""
        settings.AZURE_STORAGE_TRANSCRIPTION_INPUT_URL_SEGMENT = "temp/transcription/in"
        settings.AZURE_ACCOUNT_NAME = "testaccount"
        settings.AZURE_ACCOUNT_KEY = "testkey"
        settings.AZURE_CONTAINER = "testcontainer"

        mock_container_client = MagicMock()
        mock_container_client.list_blobs.return_value = []
        mock_blob_service = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container_client

        with patch(
            "azure.storage.blob.BlobServiceClient", return_value=mock_blob_service
        ):
            result = cleanup_dangling_transcription_blobs()

        assert result["deleted"] == 0
        assert result["skipped"] == 0
        mock_container_client.delete_blob.assert_not_called()

    def test_cleanup_transcription_blobs_deletes_old_blobs(self, settings):
        """Blobs older than 24 hours should be deleted."""
        settings.AZURE_STORAGE_TRANSCRIPTION_INPUT_URL_SEGMENT = "temp/transcription/in"
        settings.AZURE_ACCOUNT_NAME = "testaccount"
        settings.AZURE_ACCOUNT_KEY = "testkey"
        settings.AZURE_CONTAINER = "testcontainer"

        old_blob = self._make_blob("temp/transcription/in/old_audio.wav", age_hours=25)

        mock_container_client = MagicMock()
        mock_container_client.list_blobs.return_value = [old_blob]
        mock_blob_service = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container_client

        with patch(
            "azure.storage.blob.BlobServiceClient", return_value=mock_blob_service
        ):
            result = cleanup_dangling_transcription_blobs()

        mock_container_client.delete_blob.assert_called_once_with(old_blob.name)
        assert result["deleted"] == 1
        assert result["skipped"] == 0

    def test_cleanup_transcription_blobs_skips_recent_blobs(self, settings):
        """Blobs modified within the last 24 hours must NOT be deleted (may be in use)."""
        settings.AZURE_STORAGE_TRANSCRIPTION_INPUT_URL_SEGMENT = "temp/transcription/in"
        settings.AZURE_ACCOUNT_NAME = "testaccount"
        settings.AZURE_ACCOUNT_KEY = "testkey"
        settings.AZURE_CONTAINER = "testcontainer"

        recent_blob = self._make_blob(
            "temp/transcription/in/active_audio.wav", age_hours=2
        )

        mock_container_client = MagicMock()
        mock_container_client.list_blobs.return_value = [recent_blob]
        mock_blob_service = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container_client

        with patch(
            "azure.storage.blob.BlobServiceClient", return_value=mock_blob_service
        ):
            result = cleanup_dangling_transcription_blobs()

        mock_container_client.delete_blob.assert_not_called()
        assert result["deleted"] == 0
        assert result["skipped"] == 1

    def test_cleanup_transcription_blobs_mixed_ages(self, settings):
        """Only blobs older than 24 hours should be deleted; recent blobs are skipped."""
        settings.AZURE_STORAGE_TRANSCRIPTION_INPUT_URL_SEGMENT = "temp/transcription/in"
        settings.AZURE_ACCOUNT_NAME = "testaccount"
        settings.AZURE_ACCOUNT_KEY = "testkey"
        settings.AZURE_CONTAINER = "testcontainer"

        old_blob = self._make_blob("temp/transcription/in/old.wav", age_hours=48)
        recent_blob = self._make_blob("temp/transcription/in/recent.wav", age_hours=1)

        mock_container_client = MagicMock()
        mock_container_client.list_blobs.return_value = [old_blob, recent_blob]
        mock_blob_service = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container_client

        with patch(
            "azure.storage.blob.BlobServiceClient", return_value=mock_blob_service
        ):
            result = cleanup_dangling_transcription_blobs()

        mock_container_client.delete_blob.assert_called_once_with(old_blob.name)
        assert result["deleted"] == 1
        assert result["skipped"] == 1

    def test_cleanup_transcription_blobs_handles_delete_error(self, settings):
        """cleanup_dangling_transcription_blobs should continue after a delete error."""
        settings.AZURE_STORAGE_TRANSCRIPTION_INPUT_URL_SEGMENT = "temp/transcription/in"
        settings.AZURE_ACCOUNT_NAME = "testaccount"
        settings.AZURE_ACCOUNT_KEY = "testkey"
        settings.AZURE_CONTAINER = "testcontainer"

        old_blob1 = self._make_blob("temp/transcription/in/file1.wav", age_hours=48)
        old_blob2 = self._make_blob("temp/transcription/in/file2.wav", age_hours=48)

        mock_container_client = MagicMock()
        mock_container_client.list_blobs.return_value = [old_blob1, old_blob2]
        # First delete fails, second succeeds
        mock_container_client.delete_blob.side_effect = [
            Exception("Delete failed"),
            None,
        ]
        mock_blob_service = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container_client

        with patch(
            "azure.storage.blob.BlobServiceClient", return_value=mock_blob_service
        ):
            result = cleanup_dangling_transcription_blobs()

        assert result["deleted"] == 1

    def test_cleanup_transcription_blobs_missing_credentials(self, settings):
        """cleanup_dangling_transcription_blobs should return error when storage is not configured."""
        settings.AZURE_STORAGE_TRANSCRIPTION_INPUT_URL_SEGMENT = "temp/transcription/in"
        settings.AZURE_ACCOUNT_NAME = ""
        settings.AZURE_ACCOUNT_KEY = ""
        settings.AZURE_CONTAINER = ""

        result = cleanup_dangling_transcription_blobs()

        assert "error" in result
