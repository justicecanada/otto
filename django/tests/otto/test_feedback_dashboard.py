from django.urls import reverse

import pytest

from otto.models import Feedback


@pytest.mark.django_db
def test_feedback_dashboard_view(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    response = client.get(reverse("feedback_dashboard"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_feedback_stats_view(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    response = client.get(reverse("feedback_stats"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_feedback_list_view(client, all_apps_user, basic_feedback):
    user = all_apps_user()
    client.force_login(user)

    feedback_other_new = basic_feedback(user)
    feedback_other_new.status = "new"
    feedback_other_new.feedback_type = "other"
    feedback_other_new.save()

    feedback_bug_resolved = basic_feedback(user)
    feedback_bug_resolved.status = "resolved"
    feedback_bug_resolved.feedback_type = "bug"
    feedback_bug_resolved.app = "chat"
    feedback_bug_resolved.save()

    response = client.get(reverse("feedback_list"))

    assert response.status_code == 200
    assert "feedback_info" in response.context
    assert len(response.context["feedback_info"]) == 2

    # Test filtering by feedback type
    response = client.get(reverse("feedback_list"), {"feedback_type": "other"})
    assert response.status_code == 200
    assert len(response.context["feedback_info"]) == 1
    assert response.context["feedback_info"][0]["feedback"] == feedback_other_new

    # Test filtering by status
    response = client.get(reverse("feedback_list"), {"status": "resolved"})
    assert response.status_code == 200
    assert len(response.context["feedback_info"]) == 1
    assert response.context["feedback_info"][0]["feedback"] == feedback_bug_resolved

    # Test filtering by app (assuming feedback objects have an app field)
    feedback_other_new.app = "Otto"
    feedback_other_new.save()
    response = client.get(reverse("feedback_list"), {"app": "Otto"})
    assert response.status_code == 200
    assert len(response.context["feedback_info"]) == 1
    assert response.context["feedback_info"][0]["feedback"] == feedback_other_new


@pytest.mark.django_db
def test_feedback_dashboard_update_metadata(client, all_apps_user, basic_feedback):
    user = all_apps_user()
    client.force_login(user)

    feedback = basic_feedback(user)
    feedback.status = "new"
    feedback.feedback_type = "bug"
    feedback.save()

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
def test_feedback_dashboard_update_notes(client, all_apps_user, basic_feedback):
    user = all_apps_user()
    client.force_login(user)

    feedback = basic_feedback(user)
    feedback.save()

    url = reverse("feedback_dashboard_update", args=[feedback.id, "notes"])
    data = {
        "admin_notes": "Updated notes",
    }

    response = client.post(url, data)
    assert response.status_code == 200

    feedback.refresh_from_db()
    assert feedback.admin_notes == "Updated notes"


@pytest.mark.django_db
def test_feedback_dashboard_update(client, all_apps_user, basic_feedback):
    user = all_apps_user()
    client.force_login(user)
    feedback = basic_feedback(user)
    feedback.save()
    url = reverse("feedback_dashboard_update", args=[feedback.id, "metadata"])
    response = client.get(url)
    assert response.status_code == 405


@pytest.mark.django_db
def test_feedback_download_view(client, all_apps_user, basic_feedback):
    user = all_apps_user()
    client.force_login(user)

    feedback1 = basic_feedback(user)
    feedback1.status = "new"
    feedback1.feedback_type = "other"
    feedback1.app = "Otto"
    feedback1.admin_notes = "Note 1"
    feedback1.otto_version = "1.0"
    feedback1.url_context = "/app1"
    feedback1.save()

    feedback2 = basic_feedback(user)
    feedback2.status = "resolved"
    feedback2.feedback_type = "bug"
    feedback2.app = "Otto"
    feedback2.admin_notes = "Note 2"
    feedback2.otto_version = "1.1"
    feedback2.url_context = "/app2"
    feedback2.save()

    response = client.get(reverse("feedback_download"))

    assert response.status_code == 200
    assert response["Content-Disposition"] == 'attachment; filename="otto_feedback.csv"'
    assert response["Content-Type"] == "text/csv"

    content = response.content.decode("utf-8")
    lines = content.split("\n")
    assert len(lines) > 1
