from django.urls import reverse

import pytest

from otto.secure_models import AccessKey
from template_wizard.models import Report

pytestmark = pytest.mark.django_db


def test_canlii_wizard(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    response = client.get(reverse("template_wizard:index"))
    assert response.status_code == 200

    # Simulate posting the selection of starting a new report
    response = client.post(
        reverse("template_wizard:index"),
        data={"new_or_open": "new", "wizard": "canlii_wizard"},
        follow=True,  # Follow redirects
    )
    assert response.status_code == 200

    # Get the latest Report for the user
    report = Report.objects.all(AccessKey(user=user)).latest("created_at")
    assert report.wizard == "canlii_wizard"

    # Simulate posting the selection of opening an existing report
    response = client.post(
        reverse("template_wizard:index"),
        data={"new_or_open": "open", "report_id": report.id},
        follow=True,  # Follow redirects
    )
    assert response.status_code == 200

    # Delete the report by calling the delete_report view
    response = client.get(reverse("template_wizard:delete_report", args=[report.id]))
    assert response.status_code == 200
    assert not Report.objects.filter(AccessKey(user=user), id=report.id).exists()

    # TODO: Select data for the report
    # TODO: Pick a template for the report
    # TODO: Download the report
