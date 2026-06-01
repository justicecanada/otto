import tempfile

from django.urls import reverse

import pytest

from chat.models import Chat, ChatFile, Message


@pytest.mark.django_db
def test_download_file_comma(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    filename_with_comma = "test, file.txt"

    with tempfile.TemporaryDirectory() as tmpdirname:
        file_path = f"{tmpdirname}/{filename_with_comma}"
        with open(file_path, "w") as file:
            file.write("Hello")

        chat = Chat.objects.create(user=user)
        in_message = Message.objects.create(chat=chat, text="")
        chat_file = ChatFile.objects.create(
            message=in_message,
            filename=filename_with_comma,
            # eof=1, # SavedFile creation handles this? No, create handles eof kwarg locally if no saved_file provided.
            # But here ChatFile.create calls super().create.
            # ChatFile.objects.create creates SavedFile if not provided.
        )
        # Note: ChatFile.objects.create creates a SavedFile if not passed.
        # But we need to save the actual file content to that saved_file.

        chat_file.saved_file.file.save(filename_with_comma, open(file_path, "rb"))
        chat_file.saved_file.save()

        file_id = chat_file.id
        url = reverse("chat:download_file", args=[file_id])
        response = client.get(url)
        assert response.status_code == 200

        content_disposition = response.get("Content-Disposition")
        print(f"Content-Disposition: {content_disposition}")

        # This assert effectively checks if the filename is quoted or handled correctly.
        # If it is simply `filename=test, file.txt`, it is technically invalid HTTP if processed strictly,
        # but here we just check availability.
        # A better check is to see if it is quoted.

        assert (
            'filename="test, file.txt"' in content_disposition
            or "filename*=utf-8''test%2C%20file.txt" in content_disposition
        )
