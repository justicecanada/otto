"""
Test that files and related objects are cleaned up properly.
This includes librarian, chat and general Otto tests.
"""

import os
import shutil
import time

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

import pytest
from structlog import get_logger

from chat.models import Chat, ChatFile, Message
from librarian.models import DataSource, Document, Library, LibraryUserRole, SavedFile
from librarian.utils.process_engine import generate_hash
from otto.models import Notification
from otto.secure_models import AccessKey
from text_extractor.models import OutputFile, UserRequest

logger = get_logger(__name__)


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
    upload_url = reverse(
        "librarian:direct_upload", kwargs={"data_source_id": data_source.id}
    )
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
    # A document should NOT have been created since hash, filename and data source are the same
    documents = data_source.documents.all()
    assert documents.count() == 1
    # Now, change the filename and upload again
    # First, copy this file to a different name
    new_file_path = os.path.join(os.path.dirname(this_file_path), "new_file_name.txt")
    shutil.copy(this_file_path, new_file_path)
    with open(new_file_path, "rb") as f:
        response = client.post(upload_url, {"file": f})
        assert response.status_code == 200
    # Delete the file
    os.remove(new_file_path)
    # Check SavedFile NOT created
    saved_files = SavedFile.objects.all()
    assert saved_files.count() == 1
    # A document should have been created since the filename is different
    documents = data_source.documents.all()
    assert documents.count() == 2
    # Check that both documents reference the same SavedFile
    assert documents.first().saved_file == documents.last().saved_file
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
def test_reset_monthly_bonus_task(client, all_apps_user):
    """
    Test the reset_monthly_bonus task.
    """
    user = all_apps_user()
    user.monthly_bonus = 20
    user.save()
    # Test the task
    from otto.tasks import reset_monthly_bonus

    reset_monthly_bonus()
    # Check that the user has a new bonus
    user.refresh_from_db()
    assert user.monthly_bonus == 0


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
    time.sleep(3)
    # Access the chat again to update the accessed_at time
    response = client.get(reverse("chat:chat", kwargs={"chat_id": chat.id}))
    assert response.status_code == 200
    chat.refresh_from_db()
    # Check that the chat.accessed_at is now updated
    assert (chat.accessed_at - start_time).total_seconds() >= 2
    # Manually set the accessed_at time to 100 days ago
    chat.accessed_at = timezone.now() - timezone.timedelta(days=40)
    chat.save()
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
    delete_old_chats()
    # Check that the new chat is still there
    assert Chat.objects.count() == 1


@pytest.mark.django_db
def test_delete_unused_libraries_task(client, all_apps_user, basic_user):
    """
    Test the delete_unused_libraries task.
    """
    user = all_apps_user()
    # Create a Library by posting to the library route
    client.force_login(user)
    start_time = timezone.now()
    url = reverse("librarian:modal_create_library")
    response = client.post(
        url, {"name_en": "New Library", "is_public": False, "order": 1}
    )
    assert response.status_code == 200
    # Check that the library was created
    from librarian.views import get_editable_libraries

    user_libraries = get_editable_libraries(user)
    assert len(user_libraries) == 3
    library = user_libraries[2]
    library_id = library.id
    assert library is not None
    # Manually set the accessed_at time to 32 days ago
    library.accessed_at = timezone.now() - timezone.timedelta(days=32)
    library.save()
    # Test the task
    from otto.tasks import delete_unused_libraries

    delete_unused_libraries()
    # Check that the library is deleted
    assert not Library.objects.filter(id=library_id).exists()
    # Create a new library that should NOT be affected by the task
    client.force_login(user)
    url = reverse("librarian:modal_create_library")
    response = client.post(
        url, {"name_en": "New Library", "is_public": False, "order": 1}
    )
    assert response.status_code == 200
    user_libraries = get_editable_libraries(user)
    library = user_libraries[2]
    assert library is not None
    library_id = library.id
    # Manually set the accessed_at time to 32 days ago
    library.accessed_at = timezone.now() - timezone.timedelta(days=32)
    library.save()
    # Create a data source which will update library.access_at
    data_source = DataSource.objects.create(name="New Data Source", library=library)
    data_source.save()
    # Run the task again
    delete_unused_libraries()
    # Check that the new library is still there
    assert Library.objects.filter(id=library_id).exists()
    # Manually set the access_at to 32 days ago
    library.accessed_at = timezone.now() - timezone.timedelta(days=32)
    library.save()
    # Create a document which will update library.accessed_at
    document = Document.objects.create(data_source=data_source)
    document.save()
    # Run the task again
    delete_unused_libraries()
    # Check that the new library is still there
    assert Library.objects.filter(id=library_id).exists()
    # Manually set the access_at to 32 days ago
    library.accessed_at = timezone.now() - timezone.timedelta(days=32)
    library.save()
    # Delete document which will update library.accessed_at
    document.delete()
    # Check the document object is deleted
    assert not Document.objects.filter(id=document.id).exists()
    # Run the task again
    delete_unused_libraries()
    # Check that the new library is still there
    assert Library.objects.filter(id=library_id).exists()
    # Manually set the access_at to 32 days ago
    library.accessed_at = timezone.now() - timezone.timedelta(days=32)
    library.save()
    # Delete data source which will update library.accessed_at
    data_source.delete()
    # Check the data source object is deleted
    assert not DataSource.objects.filter(id=data_source.id).exists()
    # Run the task again
    delete_unused_libraries()
    # Check that the new library is still there
    assert Library.objects.filter(id=library_id).exists()
    # Manually set the access_at to 32 days ago
    library.accessed_at = timezone.now() - timezone.timedelta(days=32)
    library.save()
    user_2 = basic_user(accept_terms=True)
    client.force_login(user_2)
    library_id = library.id
    # New user needs to be admin so that they can change library roles through the form
    LibraryUserRole.objects.create(user=user_2, library=library, role="admin")
    # Change library roles which will update library.accessed_at
    url = reverse(
        "librarian:modal_manage_library_users", kwargs={"library_id": library.id}
    )
    response = client.post(url, data={"admins": user.id})
    assert response.status_code == 200
    # Run the task again
    delete_unused_libraries()
    # Check that the library is still there
    assert Library.objects.filter(id=library_id).exists()
    # Manually set the access_at to 32 days ago
    library.accessed_at = timezone.now() - timezone.timedelta(days=32)
    library.save()
    # Create a chat using the route to create it with appropriate options
    client.force_login(user)

    # Create a chat using the route to create it with appropriate options
    response = client.get(reverse("chat:qa"), follow=True)
    chat = Chat.objects.filter(user=user).order_by("-created_at").first()
    chat.options.qa_library = library
    chat.options.save()

    # Test chat_response with QA mode. This should query the Corporate library.
    message = Message.objects.create(
        chat=chat,
        text="What is the capital of Canada?",
        mode="qa",
    )
    response_message = Message.objects.create(
        chat=chat, mode="qa", is_bot=True, parent=message
    )
    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200
    # Run the task again
    delete_unused_libraries()
    # Check that the library is still there
    assert Library.objects.filter(id=library_id).exists()


@pytest.mark.django_db
def test_warn_libraries_pending_deletion_task(client, all_apps_user, basic_user):
    """
    Test the warn_libraries_pending_deletion task.
    """
    user = all_apps_user()
    # Create a Library by posting to the library route
    client.force_login(user)
    url = reverse("librarian:modal_create_library")
    for i in range(1, 5):
        response = client.post(
            url, {"name_en": f"New Library {i}", "is_public": False, "order": i}
        )
        assert response.status_code == 200
    # Check that the library was created
    from librarian.views import get_editable_libraries

    user_libraries = get_editable_libraries(user)
    assert len(user_libraries) == 6
    # Delete all Notifications
    Notification.objects.all().delete()
    # Check that all notifications have been deleted
    assert Notification.objects.all().count() == 0
    # Manually set the created libraries accessed_at time to 25 days ago
    for i in range(2, 6):
        library = user_libraries[i]
        library.accessed_at = timezone.now() - timezone.timedelta(days=25)
        library.save()
    # Test the task
    from otto.tasks import warn_libraries_pending_deletion

    warn_libraries_pending_deletion()
    # Check that notifications have been sent
    assert Notification.objects.all().count() == 4


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


@pytest.mark.django_db(transaction=True)
def test_delete_text_extractor_files_task(client, all_apps_user):

    # Ensure the "Otto admin" group exists
    group, created = Group.objects.get_or_create(name="Otto admin")

    user = all_apps_user()
    client.force_login(user)
    # Create a UserRequest
    access_key = AccessKey(user=user)

    UserRequest.objects.all(access_key=access_key).delete()
    OutputFile.objects.all(access_key=access_key).delete()

    # Grant the necessary permissions to the user for UserRequest
    content_type_user_request = ContentType.objects.get_for_model(UserRequest)
    permission_user_request, created = Permission.objects.get_or_create(
        codename="add_userrequest",
        content_type=content_type_user_request,
        name="Can add user request",
    )
    group.permissions.add(permission_user_request)
    user.groups.add(group)
    user.user_permissions.add(permission_user_request)

    user_request1 = UserRequest.objects.create(
        access_key=access_key, name="Test Request 1"
    )
    user_request2 = UserRequest.objects.create(
        access_key=access_key, name="Test Request 2"
    )

    # Grant the permissions to the user for OutputFile
    content_type_output_file = ContentType.objects.get_for_model(OutputFile)
    permission_output_file_add, created = Permission.objects.get_or_create(
        codename="add_outputfile",
        content_type=content_type_output_file,
        name="Can add output file",
    )
    permission_output_file_delete, created = Permission.objects.get_or_create(
        codename="delete_outputfile",
        content_type=content_type_output_file,
        name="Can delete output file",
    )
    user.user_permissions.add(permission_output_file_add)
    user.user_permissions.add(permission_output_file_delete)
    # Verify current time
    current_time = timezone.now()
    logger.debug(f"Current time: {current_time}")
    # Create an OutputFile
    this_file_path = os.path.abspath(__file__)
    with open(this_file_path, "rb") as f:
        content = f.read()

    output_file1 = OutputFile.objects.create(
        access_key=access_key,
        user_request=user_request1,
        txt_file=ContentFile(content, name="test_file1.txt"),
        file_name="test_file1.txt",
    )
    # Set the creation time to 40 hours ago
    user_request1.created_at = current_time - timezone.timedelta(hours=40)
    user_request1.save(access_key=access_key)
    logger.debug(f"User request 1 created_at: {user_request1.created_at}")

    output_file2 = OutputFile.objects.create(
        access_key=access_key,
        user_request=user_request2,
        pdf_file=ContentFile(content, name="test_file2.txt"),
        file_name="test_file2.txt",
    )
    # Set the creation time to 5 min ago
    user_request2.created_at = current_time - timezone.timedelta(minutes=5)
    user_request2.save(access_key=access_key)
    logger.debug(f"User request 2 created_at: {user_request2.created_at}")

    # Test the task
    from otto.tasks import delete_text_extractor_files

    delete_text_extractor_files()

    # Check that the old UserRequest is gone
    old_user_requests = UserRequest.objects.filter(
        access_key=access_key, name="Test Request 1"
    )
    assert old_user_requests.count() == 0

    # Check that the new UserRequest still exists
    new_user_requests = UserRequest.objects.filter(
        access_key=access_key, name="Test Request 2"
    )
    assert new_user_requests.count() == 1

    # Check that the associated OutputFile for the old UserRequest is gone
    old_output_files = OutputFile.objects.filter(
        access_key=access_key, file_name="test_file1.txt"
    )
    assert old_output_files.count() == 0

    # Check that the associated OutputFile for the new UserRequest still exists
    new_output_files = OutputFile.objects.filter(
        access_key=access_key, file_name="test_file2.txt"
    )
    assert new_output_files.count() == 1

    # Check media directory
    media_folder = os.path.join(settings.MEDIA_ROOT, "ocr_output_files")
    logger.debug(f"Files in media folder: {os.listdir(media_folder)}")
    assert "test_file1.txt" not in os.listdir(media_folder)
    assert "test_file2.txt" in os.listdir(media_folder)
