import json
import uuid

from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone

import pytest

from case_prep.models import Document, Session
from otto.secure_models import AccessKey

pytestmark = pytest.mark.django_db


def test_session(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    response = client.get(reverse("case_prep:index"))
    assert response.status_code == 200
    assert "sessions" in response.context

    response = client.post(reverse("case_prep:create_session"))
    assert response.status_code == 302  # Should redirect to session_detail
    session = Session.objects.all(AccessKey(user=user)).latest("created_at")
    assert session.created_by == user

    response = client.get(reverse("case_prep:session_detail", args=[session.id]))
    assert response.status_code == 200
    assert "session" in response.context
    assert response.context["session"].id == session.id

    # Mocking a file upload
    file_content = ContentFile(b"Sample content", "sample.txt")
    file_content.content_type = "text/plain"

    response = client.post(
        reverse("case_prep:upload_files"),
        {
            "session_id": session.id,
            "documents": [file_content],
        },
        follow=True,
    )

    access_key = AccessKey(user)
    assert response.status_code == 200
    documents = Document.objects.filter(access_key, session=session)
    assert documents.count() == 1
    assert documents[0].name == "sample"

    response = client.post(reverse("case_prep:delete_session", args=[session.id]))
    assert response.status_code == 200
    assert not Session.objects.filter(access_key, id=session.id).exists()
