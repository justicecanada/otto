from types import SimpleNamespace
from unittest.mock import patch

from django.urls import reverse

import pytest

from otto.models import Cost

from chat.llm import OttoLLM


@pytest.mark.django_db
def test_enabling_load_test(client, basic_user, all_apps_user):
    user = basic_user(accept_terms=True)
    client.force_login(user)
    response = client.get(reverse("enable_load_testing"))
    # This shouldn't work
    assert response.status_code == 302
    assert user.notifications.count() == 1
    # Now test with a user that has the correct permissions (all_apps_user)
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("enable_load_testing"))
    assert response.status_code == 200
    # Disable the load test
    response = client.get(reverse("disable_load_testing"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_load_tests(client, all_apps_user):
    # Shouldn't need to be logged in at all
    response = client.get(reverse("load_test"))
    # But since load test isn't enabled, should get a 403
    assert response.status_code == 403
    # Now, enable the load test (as admin user)
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("enable_load_testing"))
    assert response.status_code == 200

    # Logout and try again
    client.logout()
    response = client.get(reverse("load_test"))
    assert response.status_code == 200

    # Only mock tokens when llm_call or embed_text is used
    with patch("chat.llm.OttoLLM.complete", return_value="mocked response"):
        # llm_call cases
        with (
            patch.object(OttoLLM, "input_token_count", 1),
            patch.object(OttoLLM, "output_token_count", 1),
        ):
            response = client.get(reverse("load_test"), {"llm_call": "gpt-4.1"})
            assert response.status_code == 200
            # Optionally check the response content if needed
            # assert "mocked response" in response.content.decode()
        with (
            patch.object(OttoLLM, "input_token_count", 1),
            patch.object(OttoLLM, "output_token_count", 1),
        ):
            response = client.get(reverse("load_test"), {"llm_call": ""})
            assert response.status_code == 200
            # Optionally check the response content if needed
            # assert "mocked response" in response.content.decode()

    # embed_text case
    response = client.get(reverse("load_test"), {"embed_text": "", "mock_llm": "True"})
    assert response.status_code == 200

    # Try some different load tests to exercise the view
    response = client.get(reverse("load_test"), {"user_library_permissions": ""})
    assert response.status_code == 200
    # response = client.get(reverse("load_test"), {"user_library_permissions": "", "heavy": ""})
    # assert response.status_code == 200
    response = client.get(reverse("load_test"), {"sleep": 1})
    assert response.status_code == 200
    response = client.get(reverse("load_test"), {"error": ""})
    assert response.status_code == 500
    # Should add 1 cost object (or 0 if laws table doesn't exist)
    response = client.get(reverse("load_test"), {"query_laws": ""})
    assert response.status_code == 200

    # Check that there are 4 or 5 cost objects with feature "load_test"
    # (5 if query_laws succeeded, 4 if it failed due to missing table)
    cost_count = Cost.objects.filter(feature="load_test").count()
    assert cost_count in [4, 5]

    # Login as admin user and disable the load test
    client.force_login(user)
    response = client.get(reverse("disable_load_testing"))
    assert response.status_code == 200
    # Now try to access the load test again
    response = client.get(reverse("load_test"))
    assert response.status_code == 403


@pytest.mark.django_db
def test_celery_load_tests(client, all_apps_user):
    # Enable the load test (as admin user)
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("enable_load_testing"))
    assert response.status_code == 200

    client.logout()

    # Try some different load tests to exercise the view
    response = client.get(reverse("load_test"), {"celery_sleep": 1})
    assert response.status_code == 200
    response = client.get(reverse("load_test"), {"celery_sleep": 1, "show_queue": ""})
    assert response.status_code == 200

    # Login as admin user and disable the load test
    client.force_login(user)
    response = client.get(reverse("disable_load_testing"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_celery_priority_sleep_load_test(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("enable_load_testing"))
    assert response.status_code == 200

    client.logout()

    with patch("otto.tasks.sleep_seconds.apply_async") as mock_apply_async:
        mock_apply_async.return_value = SimpleNamespace(id="priority-task-1")

        response = client.get(
            reverse("load_test"),
            {
                "celery_priority_sleep": 20,
                "queue": "heavy",
                "priority": "3",
                "count": 3,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["task_id"] == "priority-task-1"
        assert payload["queue"] == "heavy"
        assert payload["priority"] == 3
        assert payload["seconds"] == 20
        assert payload["count"] == 3
        assert mock_apply_async.call_count == 3

    invalid_queue_response = client.get(
        reverse("load_test"),
        {
            "celery_priority_sleep": 20,
            "queue": "not-a-queue",
        },
    )
    assert invalid_queue_response.status_code == 400

    invalid_priority_response = client.get(
        reverse("load_test"),
        {
            "celery_priority_sleep": 20,
            "queue": "light",
            "priority": "urgent",
        },
    )
    assert invalid_priority_response.status_code == 400

    invalid_count_response = client.get(
        reverse("load_test"),
        {
            "celery_priority_sleep": 20,
            "queue": "light",
            "priority": "low",
            "count": 0,
        },
    )
    assert invalid_count_response.status_code == 400

    client.force_login(user)
    response = client.get(reverse("disable_load_testing"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_load_test_laws_text_extractor_contention_helpers(client, all_apps_user):
    from laws.models import JobStatus

    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("enable_load_testing"))
    assert response.status_code == 200

    job_status = JobStatus.objects.singleton()
    job_status.status = "not_started"
    job_status.save(update_fields=["status"])

    client.logout()

    with (
        patch("laws.tasks.update_laws.delay") as mock_update_laws_delay,
        patch(
            "text_extractor.tasks.process_document_merge.apply_async"
        ) as mock_merge_apply_async,
        patch(
            "text_extractor.tasks.process_document_merge.AsyncResult"
        ) as mock_merge_async_result,
    ):
        mock_update_laws_delay.return_value = SimpleNamespace(id="laws-task-1")
        mock_merge_apply_async.return_value = SimpleNamespace(id="merge-task-1")
        mock_merge_async_result.return_value = SimpleNamespace(
            status="STARTED", info=None, ready=lambda: False
        )

        start_laws_response = client.get(
            reverse("load_test"), {"laws_load_start_small": "1"}
        )
        assert start_laws_response.status_code == 200
        assert start_laws_response.json()["started"] is True
        assert start_laws_response.json()["task_id"] == "laws-task-1"

        laws_status_response = client.get(
            reverse("load_test"), {"laws_load_status": "1"}
        )
        assert laws_status_response.status_code == 200
        assert laws_status_response.json()["ok"] is True
        assert "mid_run" in laws_status_response.json()

        enqueue_response = client.get(
            reverse("load_test"), {"text_extractor_merge_enqueue": "1"}
        )
        assert enqueue_response.status_code == 200
        assert enqueue_response.json()["enqueued"] is True
        assert enqueue_response.json()["task_id"] == "merge-task-1"

        task_status_response = client.get(
            reverse("load_test"), {"text_extractor_task_status": "merge-task-1"}
        )
        assert task_status_response.status_code == 200
        assert task_status_response.json()["ok"] is True
        assert task_status_response.json()["status"] == "STARTED"

    client.force_login(user)
    response = client.get(reverse("disable_load_testing"))
    assert response.status_code == 200
