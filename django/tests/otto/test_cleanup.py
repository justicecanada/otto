"""
Test that files and related objects are cleaned up properly.
This includes librarian, chat and general Otto tests.
"""

import os
import time

from django.conf import settings
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

import pytest

from chat.models import Chat, ChatFile, Message
from librarian.models import DataSource, Document, Library, SavedFile
from librarian.utils.process_engine import generate_hash


@pytest.mark.django_db
def test_redundant_librarian_upload(client, all_apps_user):
    """
    Upload a file through Librarian modal.
    Check media directory
    Upload same file again.
    Check media directory - still should be just 1 instance of the file though there are now 2 documents
    Delete one of the documents in Librarian modal.
    File should still be there, since there is a reference from the other Document object.
    Delete the other Document
    File should be gone now.
    """
    user = all_apps_user()
    client.force_login(user)
    # Create a DataSource in the user library (manually)
    user_library = user.personal_library
    data_source = DataSource.objects.create(
        library=user_library,
        name="Test Data Source",
    )
    upload_url = reverse("librarian:upload", kwargs={"data_source_id": data_source.id})
    # Test uploading this file
    this_file_path = os.path.abspath(__file__)
    with open(this_file_path, "rb") as f:
        response = client.post(upload_url, {"file": f})
        assert response.status_code == 200
    # Check SavedFile created
    saved_files = SavedFile.objects.all()
    assert saved_files.count() == 1
    # Check Document created
    documents = data_source.documents.all()
    assert documents.count() == 1
    # Check media directory
    assert os.path.exists(saved_files[0].file.path)
    # Upload the same file again
    with open(this_file_path, "rb") as f:
        response = client.post(upload_url, {"file": f})
        assert response.status_code == 200
    # Check SavedFile NOT created
    saved_files = SavedFile.objects.all()
    assert saved_files.count() == 1
    # Check Document created
    documents = data_source.documents.all()
    assert documents.count() == 2
    # Check that both documents reference the same SavedFile
    assert documents.first().file == documents.last().file
    # Check media directory. There should be only 1 file
    folder = os.path.dirname(saved_files[0].file.path)
    assert len(os.listdir(folder)) == 1
    # Delete one of the documents
    document = documents.first()
    document.delete()
    # Check SavedFile still exists
    saved_files = SavedFile.objects.all()
    assert saved_files.count() == 1
    # Check media directory
    assert os.path.exists(saved_files[0].file.path)
    # Delete the other document
    document = documents.last()
    document.delete()
    # Check SavedFile is gone
    saved_files = SavedFile.objects.all()
    assert saved_files.count() == 0
    # Check media directory
    len(os.listdir(folder)) == 0


@pytest.mark.django_db
def test_redundant_chat_upload(client, all_apps_user):
    """
    Start a new chat.
    Upload a file using chunk_upload route.
    Check the media directory for the file
    Upload the same file again.
    Check the media directory - there should still be only 1 file.
    Both files should be in the library.
    Delete the chat
    Check the media directory - files should be gone.
    """
    user = all_apps_user()
    client.force_login(user)
    # Start a new chat
    response = client.get(reverse("chat:new_chat"))
    assert response.status_code == 302
    # Check that the chat was created
    chat = user.chat_set.first()
    assert chat is not None

    # Create a message in the chat
    message = Message.objects.create(chat=chat)
    # Upload a file
    this_file_path = os.path.abspath(__file__)
    # Hash of this file
    with open(this_file_path, "rb") as f:
        sha256_hash = generate_hash(f.read())
    # Try uploading the file through the chat "chunk upload" mechanism
    # We don't need to worry about the actual chunking; just upload the whole thing.
    upload_url = reverse("chat:chunk_upload", kwargs={"message_id": message.id})
    with open(this_file_path, "rb") as f:
        response = client.post(
            upload_url,
            {
                "file": f,
                "hash": sha256_hash,
                "filename": os.path.basename(this_file_path),
                "end": 1,
                "file_id": "null",
                "nextSlice": "null",
                "content_type": "text/plain",
            },
        )
        assert response.status_code == 200

    # Check that a SavedFile was created
    saved_files = SavedFile.objects.all()
    assert saved_files.count() == 1
    # Check that a ChatFile was created
    chat_files = ChatFile.objects.all()
    assert chat_files.count() == 1
    # Check media directory
    assert os.path.exists(saved_files[0].file.path)

    # Upload again, this time indicating the upload is not complete (end=0)
    # but since the hash is included, it should be recognized as a duplicate
    upload_url = reverse("chat:chunk_upload", kwargs={"message_id": message.id})
    with open(this_file_path, "rb") as f:
        response = client.post(
            upload_url,
            {
                "file": f,
                "hash": sha256_hash,
                "filename": os.path.basename(this_file_path),
                "end": 0,
                "file_id": "null",
                "nextSlice": 123,
                "content_type": "text/plain",
            },
        )
        assert response.status_code == 200
    # Check there is still only one SavedFile
    saved_files = SavedFile.objects.all()
    assert saved_files.count() == 1
    # Check that there are now two ChatFiles
    chat_files = ChatFile.objects.all()
    assert chat_files.count() == 2
    # Check that both ChatFiles references the SavedFile
    assert chat_files.first().saved_file == saved_files.first()
    # Check media directory
    assert os.path.exists(saved_files[0].file.path)
    # Make sure there is still only one file
    folder = os.path.dirname(saved_files[0].file.path)
    assert len(os.listdir(folder)) == 1
    # Delete the chat
    chat.delete()
    # Check that the SavedFile is gone
    saved_files = SavedFile.objects.all()
    assert saved_files.count() == 0
    # Check media directory
    assert len(os.listdir(folder)) == 0


@pytest.mark.django_db
def test_reset_weekly_bonus_task(client, all_apps_user):
    """
    Test the reset_weekly_bonus task.
    """
    user = all_apps_user()
    user.weekly_bonus = 20
    user.save()
    if settings.IS_RUNNING_IN_GITHUB:
        # No Redis, so we test the code directly
        from otto.models import User

        User.objects.update(weekly_bonus=0)
    else:
        # Test the task
        from otto.tasks import reset_weekly_bonus

        reset_weekly_bonus()
    # Check that the user has a new bonus
    user.refresh_from_db()
    assert user.weekly_bonus == 0


@pytest.mark.django_db
def test_delete_old_chats_task(client, all_apps_user):
    """
    Test the delete_old_chats task.
    """
    Chat.objects.all().delete()
    user = all_apps_user()
    # Create a chat by hitting the chat route
    client.force_login(user)
    start_time = timezone.now()
    response = client.get(reverse("chat:new_chat"))
    assert response.status_code == 302
    # Check that the chat was created
    chat = user.chat_set.first()
    chat_id = chat.id
    assert chat is not None
    # Check that the chat.accessed_at is close to the start time
    assert (chat.accessed_at - start_time).total_seconds() < 2
    time.sleep(3)
    # Access the chat again to update the accessed_at time
    response = client.get(reverse("chat:chat", kwargs={"chat_id": chat.id}))
    assert response.status_code == 200
    chat.refresh_from_db()
    # Check that the chat.accessed_at is now updated
    assert (chat.accessed_at - start_time).total_seconds() >= 2
    # Manually set the accessed_at time to 100 days ago
    chat.accessed_at = timezone.now() - timezone.timedelta(days=100)
    chat.save()
    if settings.IS_RUNNING_IN_GITHUB:
        # No Redis, so we test the code directly
        call_command("delete_old_chats")
    else:
        # Test the task
        from otto.tasks import delete_old_chats

        delete_old_chats()
    # Check that the chat is gone - there should now be no chats
    chat = Chat.objects.filter(id=chat_id).first()
    assert chat is None
    assert Chat.objects.count() == 0
    # Create a new chat that should NOT be affected by the task
    response = client.get(reverse("chat:new_chat"))
    assert response.status_code == 302
    chat = user.chat_set.first()
    assert chat is not None
    assert Chat.objects.count() == 1
    # Run the task again
    if settings.IS_RUNNING_IN_GITHUB:
        # No Redis, so we test the code directly
        call_command("delete_old_chats")
    else:
        delete_old_chats()
    # Check that the new chat is still there
    assert Chat.objects.count() == 1


@pytest.mark.django_db
def test_delete_empty_chats_task(client, all_apps_user):
    """
    Create an empty chat, and a non-empty chat.
    Set them to be 2 days old (1 day retention for empty chats).
    Run the delete_empty_chats task.
    Check that the empty chat is gone, but the non-empty chat remains.
    """
    user = all_apps_user()
    # Create an empty chat
    empty_chat = Chat.objects.create(user=user)
    empty_chat.accessed_at = timezone.now() - timezone.timedelta(days=2)
    empty_chat.save()
    empty_chat_id = empty_chat.id
    # Create a non-empty chat
    non_empty_chat = Chat.objects.create(user=user)
    non_empty_chat.accessed_at = timezone.now() - timezone.timedelta(days=2)
    non_empty_chat.save()
    non_empty_chat_id = non_empty_chat.id
    Message.objects.create(chat=non_empty_chat)
    # Create a too-new empty chat
    too_new_empty_chat = Chat.objects.create(user=user)
    too_new_empty_chat_id = too_new_empty_chat.id
    # Test the task
    if settings.IS_RUNNING_IN_GITHUB:
        # No Redis, so we test the code directly
        call_command("delete_empty_chats")
    else:
        # Test the task
        from otto.tasks import delete_empty_chats

        delete_empty_chats()
    # Check that the 2 day old empty chat is gone
    empty_chat = Chat.objects.filter(id=empty_chat_id).first()
    assert empty_chat is None
    # Check that the 2 day old non-empty chat remains
    non_empty_chat = Chat.objects.filter(id=non_empty_chat_id).first()
    assert non_empty_chat is not None
    # Check that the too-new empty chat remains
    too_new_empty_chat = Chat.objects.filter(id=too_new_empty_chat_id).first()
    assert too_new_empty_chat is not None
