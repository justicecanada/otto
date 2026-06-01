import uuid
from types import SimpleNamespace

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

import pytest
from translate.models import InputFile, OutputFile, UserRequest

from otto.secure_models import AccessKey


def _grant_translate_creates(access_key):
    UserRequest.grant_create_to(access_key)
    OutputFile.grant_create_to(access_key)
    InputFile.grant_create_to(access_key)


@pytest.mark.django_db
def test_translate_index_renders(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    response = client.get(reverse("translate:index"))

    assert response.status_code == 200
    assert response.context["active_app"] == "translate"
    assert response.context["hide_breadcrumbs"] is True
    assert response.context["show_output"] is False


@pytest.mark.django_db
def test_translate_index_recommends_ai_assistant_with_chat_next_link(
    client, all_apps_user
):
    user = all_apps_user()
    client.force_login(user)

    response = client.get(reverse("translate:index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "GPT translation in the AI Assistant" in content
    assert f'href="{reverse("chat_next:new_chat")}"' in content


@pytest.mark.django_db
def test_translate_index_recommends_ai_assistant_with_legacy_chat_link(
    client, basic_user
):
    user = basic_user(accept_terms=True)
    client.force_login(user)

    response = client.get(reverse("translate:index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "GPT translation in the AI Assistant" in content
    assert f'href="{reverse("chat:translate")}"' in content


@pytest.mark.django_db
def test_translate_text_empty_returns_empty_output(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    response = client.post(
        reverse("translate:translate_text"),
        {"source_text": "   ", "source_lang": "en", "target_lang": "fr"},
    )

    assert response.status_code == 200
    assert response.context["translated_text"] == ""


@pytest.mark.django_db
def test_translate_text_success_returns_translation_and_cost(
    client, all_apps_user, monkeypatch
):
    user = all_apps_user()
    client.force_login(user)

    monkeypatch.setattr("translate.views.translate_text_azure", lambda *_: "Bonjour")
    monkeypatch.setattr(
        "translate.views.Cost.objects.new",
        lambda **_: SimpleNamespace(usd_cost=0.12),
    )

    response = client.post(
        reverse("translate:translate_text"),
        {"source_text": "Hello", "source_lang": "en", "target_lang": "fr"},
    )

    assert response.status_code == 200
    assert response.context["translated_text"] == "Bonjour"
    assert response.context["usd_cost"] is not None


@pytest.mark.django_db
def test_translate_text_failure_returns_friendly_error(
    client, all_apps_user, monkeypatch
):
    user = all_apps_user()
    client.force_login(user)

    def _boom(*args, **kwargs):
        raise RuntimeError("translation backend down")

    monkeypatch.setattr("translate.views.translate_text_azure", _boom)

    response = client.post(
        reverse("translate:translate_text"),
        {"source_text": "Hello", "source_lang": "en", "target_lang": "fr"},
    )

    assert response.status_code == 200
    assert "Translation error" in response.context["translated_text"]
    assert response.context["usd_cost"] is None


@pytest.mark.django_db
def test_translate_document_without_files_returns_error(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    response = client.post(reverse("translate:translate_document"), {"language": "fr"})

    assert response.status_code == 200
    assert "No file uploaded" in response.context["error"]


@pytest.mark.django_db
def test_translate_document_large_file_marks_failure_and_skips_task(
    client, all_apps_user, monkeypatch
):
    user = all_apps_user()
    client.force_login(user)

    # Keep test tiny by lowering threshold instead of uploading >40MB.
    monkeypatch.setattr("translate.views.MAX_FILE_SIZE", 5)

    apply_async_calls = []

    def _fake_apply_async(**kwargs):
        apply_async_calls.append(kwargs)
        return SimpleNamespace(id="task-should-not-run")

    monkeypatch.setattr(
        "translate.views.translate_document_task.apply_async", _fake_apply_async
    )

    uploaded = SimpleUploadedFile(
        "too-large.txt",
        b"123456",  # 6 bytes > patched MAX_FILE_SIZE (5)
        content_type="text/plain",
    )

    response = client.post(
        reverse("translate:translate_document"),
        {"language": "fr", "file": uploaded},
    )

    assert response.status_code == 200
    assert response.context["poll_url"] is None
    assert len(response.context["output_files"]) == 1
    assert response.context["output_files"][0].status == "FAILURE"
    assert "Maximum size is 40 MB" in response.context["output_files"][0].error_message
    assert apply_async_calls == []


@pytest.mark.django_db
def test_translate_document_valid_file_queues_task(client, all_apps_user, monkeypatch):
    user = all_apps_user()
    client.force_login(user)

    monkeypatch.setattr(
        "translate.views.translate_document_task.apply_async",
        lambda **_: SimpleNamespace(id="fake-task-id"),
    )

    uploaded = SimpleUploadedFile("input.txt", b"hello", content_type="text/plain")

    response = client.post(
        reverse("translate:translate_document"),
        {"language": "fr", "file": uploaded},
    )

    assert response.status_code == 200
    assert response.context["poll_url"] is not None
    assert len(response.context["output_files"]) == 1

    output_file = response.context["output_files"][0]
    assert output_file.celery_task_ids == ["fake-task-id"]


@pytest.mark.django_db
def test_poll_tasks_pending_keeps_polling(client, all_apps_user, monkeypatch):
    user = all_apps_user()
    client.force_login(user)

    access_key = AccessKey(user=user)
    _grant_translate_creates(access_key)

    user_request = UserRequest.objects.create(
        access_key=access_key,
        name="request-1",
        source_lang="en",
        target_lang="fr",
    )
    output_file = OutputFile.objects.create(
        access_key=access_key,
        file_name="pending.txt",
        user_request=user_request,
        celery_task_ids=["task-1"],
    )

    monkeypatch.setattr(
        "translate.views.translate_document_task.AsyncResult",
        lambda task_id: SimpleNamespace(status="PENDING"),
    )

    response = client.get(reverse("translate:poll_tasks", args=[user_request.id]))

    assert response.status_code == 200
    returned_file = next(
        f for f in response.context["output_files"] if f.id == output_file.id
    )
    assert returned_file.status == "IDLING"
    assert response.context["poll_url"] is not None


@pytest.mark.django_db
def test_poll_tasks_error_without_result_file_sets_failure(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    access_key = AccessKey(user=user)
    _grant_translate_creates(access_key)

    user_request = UserRequest.objects.create(
        access_key=access_key,
        name="request-2",
        source_lang="en",
        target_lang="fr",
    )
    output_file = OutputFile.objects.create(
        access_key=access_key,
        file_name="failed.txt",
        user_request=user_request,
        celery_task_ids=["task-2"],
        error_message="Parser failed",
    )

    response = client.get(reverse("translate:poll_tasks", args=[user_request.id]))

    assert response.status_code == 200
    returned_file = next(
        f for f in response.context["output_files"] if f.id == output_file.id
    )
    assert returned_file.status == "FAILURE"
    assert response.context["poll_url"] is None


@pytest.mark.django_db
def test_download_document_returns_not_found_for_unknown_file(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    response = client.get(reverse("translate:download_document", args=[uuid.uuid4()]))

    assert response.status_code == 404
    assert "File not found" in response.content.decode()


@pytest.mark.django_db
def test_download_document_returns_not_ready_when_no_output_file_blob(
    client, all_apps_user
):
    user = all_apps_user()
    client.force_login(user)

    access_key = AccessKey(user=user)
    _grant_translate_creates(access_key)

    user_request = UserRequest.objects.create(
        access_key=access_key,
        name="request-3",
        source_lang="en",
        target_lang="fr",
    )
    output_file = OutputFile.objects.create(
        access_key=access_key,
        file_name="ready-later.txt",
        user_request=user_request,
        celery_task_ids=[],
    )

    response = client.get(reverse("translate:download_document", args=[output_file.id]))

    assert response.status_code == 404
    assert "File not ready" in response.content.decode()


@pytest.mark.django_db
def test_download_document_returns_binary_attachment(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    access_key = AccessKey(user=user)
    _grant_translate_creates(access_key)

    user_request = UserRequest.objects.create(
        access_key=access_key,
        name="request-4",
        source_lang="en",
        target_lang="fr",
    )
    output_file = OutputFile.objects.create(
        access_key=access_key,
        file_name="translated.txt",
        user_request=user_request,
        celery_task_ids=[],
    )
    output_file.file.save("translated.txt", ContentFile(b"bonjour"), save=False)
    output_file.save(access_key=access_key)

    response = client.get(reverse("translate:download_document", args=[output_file.id]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/octet-stream"
    assert 'attachment; filename="translated.txt"' == response["Content-Disposition"]
    assert b"".join(response.streaming_content) == b"bonjour"


@pytest.mark.django_db
def test_poll_tasks_shows_download_all_for_multiple_ready_files(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    access_key = AccessKey(user=user)
    _grant_translate_creates(access_key)

    user_request = UserRequest.objects.create(
        access_key=access_key,
        name="request-5",
        source_lang="en",
        target_lang="fr",
    )
    first_output = OutputFile.objects.create(
        access_key=access_key,
        file_name="translated-1.txt",
        user_request=user_request,
        celery_task_ids=[],
    )
    first_output.file.save("translated-1.txt", ContentFile(b"bonjour"), save=False)
    first_output.save(access_key=access_key)

    second_output = OutputFile.objects.create(
        access_key=access_key,
        file_name="translated-2.txt",
        user_request=user_request,
        celery_task_ids=[],
    )
    second_output.file.save("translated-2.txt", ContentFile(b"salut"), save=False)
    second_output.save(access_key=access_key)

    response = client.get(reverse("translate:poll_tasks", args=[user_request.id]))

    assert response.status_code == 200
    assert response.context["show_download_all"] is True
    assert "Download all" in response.content.decode()


@pytest.mark.django_db
def test_poll_tasks_hides_download_all_for_single_ready_file(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    access_key = AccessKey(user=user)
    _grant_translate_creates(access_key)

    user_request = UserRequest.objects.create(
        access_key=access_key,
        name="request-6",
        source_lang="en",
        target_lang="fr",
    )
    output_file = OutputFile.objects.create(
        access_key=access_key,
        file_name="translated-only.txt",
        user_request=user_request,
        celery_task_ids=[],
    )
    output_file.file.save("translated-only.txt", ContentFile(b"bonjour"), save=False)
    output_file.save(access_key=access_key)

    response = client.get(reverse("translate:poll_tasks", args=[user_request.id]))

    assert response.status_code == 200
    assert response.context["show_download_all"] is False
    assert "Download all" not in response.content.decode()
