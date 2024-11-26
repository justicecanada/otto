from django.urls import reverse

import pytest

from otto.models import Feedback


@pytest.mark.django_db
def test_feedback_list_view(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create some feedback objects for testing
    feedback_other_new = Feedback.objects.create(
        created_by=user,
        feedback_message="Test feedback 1",
        status="new",
        feedback_type="other",
    )
    feedback_bug_resolved = Feedback.objects.create(
        created_by=user,
        feedback_message="Test feedback 2",
        status="resolved",
        feedback_type="bug",
    )

    response = client.get(reverse("feedback_list"))

    assert response.status_code == 200
    assert "feedback_info" in response.context
    assert len(response.context["feedback_info"]) == 2

    # Test filtering by feedback type
    response = client.post(reverse("feedback_list"), {"feedback_type": "other"})
    assert response.status_code == 200
    assert len(response.context["feedback_info"]) == 1
    assert response.context["feedback_info"][0]["feedback"] == feedback_other_new

    # Test filtering by status
    response = client.post(reverse("feedback_list"), {"status": "resolved"})
    assert response.status_code == 200
    assert len(response.context["feedback_info"]) == 1
    assert response.context["feedback_info"][0]["feedback"] == feedback_bug_resolved

    # Test filtering by app (assuming feedback objects have an app field)
    feedback_other_new.app = "Otto"
    feedback_other_new.save()
    response = client.post(reverse("feedback_list"), {"app": "Otto"})
    assert response.status_code == 200
    assert len(response.context["feedback_info"]) == 1
    assert response.context["feedback_info"][0]["feedback"] == feedback_other_new


@pytest.mark.django_db
def test_feedback_dashboard_update_metadata(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    feedback = Feedback.objects.create(
        created_by=user,
        feedback_message="Test feedback",
        status="new",
        feedback_type="bug",
    )

    url = reverse("feedback_dashboard_update", args=[feedback.id, "metadata"])
    data = {
        "status": "resolved",
        "feedback_type": "other",
    }

    response = client.post(url, data)
    assert response.status_code == 200

    feedback.refresh_from_db()
    assert feedback.status == "resolved"
    assert feedback.feedback_type == "other"


@pytest.mark.django_db
def test_feedback_dashboard_update_notes(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    feedback = Feedback.objects.create(
        created_by=user,
        feedback_message="Test feedback",
        status="new",
        feedback_type="bug",
    )

    url = reverse("feedback_dashboard_update", args=[feedback.id, "notes"])
    data = {
        "admin_notes": "Updated notes",
    }

    response = client.post(url, data)
    assert response.status_code == 200

    feedback.refresh_from_db()
    assert feedback.admin_notes == "Updated notes"


@pytest.mark.django_db
def test_feedback_download_view(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Create some feedback objects for testing
    feedback1 = Feedback.objects.create(
        created_by=user,
        feedback_message="Test feedback 1",
        status="new",
        feedback_type="other",
        app="Otto",
        admin_notes="Note 1",
        otto_version="1.0",
        url_context="/app1",
    )
    feedback2 = Feedback.objects.create(
        created_by=user,
        feedback_message="Test feedback 2",
        status="resolved",
        feedback_type="bug",
        app="Otto",
        admin_notes="Note 2",
        otto_version="1.1",
        url_context="/app2",
    )

    response = client.get(reverse("feedback_download"))

    assert response.status_code == 200
    assert response["Content-Disposition"] == 'attachment; filename="otto_feedback.csv"'
    assert response["Content-Type"] == "text/csv"

    content = response.content.decode("utf-8")
    lines = content.split("\n")
    assert len(lines) > 1
