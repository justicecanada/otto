import datetime
import os

from django.urls import reverse

import numpy as np
import pytest
from structlog.contextvars import bind_contextvars

from otto.models import Cost, Group, Notification, User


@pytest.mark.django_db
def test_access_manage_users(client, basic_user, all_apps_user):
    user = basic_user(accept_terms=True)
    client.force_login(user)
    response = client.get(reverse("manage_users"))
    assert response.status_code == 302
    # Should be redirected back to index page since this isn't allowed
    assert response.url == reverse("index")
    # Notification should have been created
    notification = Notification.objects.get(user=user)
    assert reverse("manage_users") in notification.text

    # Now test with a user that has the correct permissions (all_apps_user)
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("manage_users"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_modify_user(client, basic_user, all_apps_user):
    user = basic_user(username="basic_user", accept_terms=True)
    admin_user = all_apps_user()
    client.force_login(admin_user)

    group_ids = Group.objects.values_list("id", flat=True)

    # Modify the basic_user
    response = client.post(
        reverse("manage_users"),
        data={
            "upn": [user.id],
            "group": [group_ids[0], group_ids[1]],
            "monthly_max": 10,
            "monthly_bonus": 0,
        },
    )
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.groups.count() == 2

    # Modify multiple users
    user2 = basic_user(username="basic_user2", accept_terms=True)
    response = client.post(
        reverse("manage_users"),
        data={
            "upn": [user.id, user2.id],
            "group": [group_ids[0], group_ids[1], group_ids[2]],
            "monthly_max": 20,
            "monthly_bonus": 10,
        },
    )
    assert response.status_code == 200
    user.refresh_from_db()
    user2.refresh_from_db()
    assert user.groups.count() == 3
    assert user2.groups.count() == 3


@pytest.mark.django_db
def test_get_user_form(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    response = client.get(reverse("manage_users_form"))
    assert response.status_code == 200

    response = client.get(reverse("manage_users_form", kwargs={"user_id": user.id}))
    assert response.status_code == 200


@pytest.mark.django_db
def test_manage_users_upload(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    response = client.post(reverse("upload_users"), data={})
    assert response.status_code == 302
    assert response.url == reverse("manage_users")

    # Test with a csv file ("users.csv" in this directory)
    """
    upn,pilot_id,roles,monthly_max
    Firstname.Lastname@justice.gc.ca,bac,AI Assistant user|Text Extractor user,100
    """
    this_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(this_dir, "users.csv"), "rb") as file:
        response = client.post(reverse("upload_users"), data={"csv_file": file})
    assert response.status_code == 302
    assert response.url == reverse("manage_users")
    # Check that the users were created
    new_user = User.objects.filter(upn="Firstname.Lastname@justice.gc.ca")
    assert new_user.exists()
    new_user = new_user.first()
    assert new_user.groups.count() == 2
    assert new_user.first_name == "Firstname"
    assert new_user.last_name == "Lastname"
    assert new_user.email == "Firstname.Lastname@justice.gc.ca"


@pytest.mark.django_db
def test_manage_users_download(client, all_apps_user, basic_user):
    user = all_apps_user()
    client.force_login(user)

    # Create a few basic users and add them to random groups
    group_ids = Group.objects.values_list("id", flat=True)
    u = basic_user(username="user1", accept_terms=True)
    for group_id in np.random.choice(group_ids, min(3, len(group_ids)), replace=False):
        u.groups.add(group_id)
    u = basic_user(username="user2", accept_terms=True)
    for group_id in np.random.choice(group_ids, min(2, len(group_ids)), replace=False):
        u.groups.add(group_id)
    u = basic_user(username="user3", accept_terms=True)
    for group_id in np.random.choice(group_ids, min(4, len(group_ids)), replace=False):
        u.groups.add(group_id)

    users = User.objects.all().values_list(
        "upn", "pilot_id", "groups__name", "monthly_max"
    )

    response = client.get(reverse("download_users"))
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert "attachment" in response["Content-Disposition"]
    # Save the file to check its contents
    with open("users.csv", "wb") as file:
        file.write(response.content)

    # Upload it
    with open("users.csv", "rb") as file:
        response = client.post(reverse("upload_users"), data={"csv_file": file})
    assert response.status_code == 302

    # Check that the users are unchanged
    updated_users = User.objects.all().values_list(
        "upn", "pilot_id", "groups__name", "monthly_max"
    )
    assert sorted(list(users)) == sorted(list(updated_users))
    os.remove("users.csv")


@pytest.mark.django_db
def test_get_cost_dashboard(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("cost_dashboard"))
    assert response.status_code == 200

    # Create some costs
    bind_contextvars(user_id=user.id, feature="chat")
    for _ in range(5):
        Cost.objects.new("gpt-4o-in", 100)
        Cost.objects.new("gpt-4o-out", 200)

    # Now try GET requests with some different parameters
    x_axes = ["day", "week", "month", "feature", "pilot", "user", "cost_type"]
    date_groups = [
        "all",
        "last_90_days",
        "last_30_days",
        "last_7_days",
        "today",
        "custom",
    ]
    cost_types = ["all", 1]

    for x_axis in x_axes:
        for date_group in date_groups:
            for download in [True, False]:
                if date_group == "custom":
                    for cost_type in cost_types:
                        response = client.get(
                            reverse("cost_dashboard"),
                            data={
                                "x_axis": x_axis,
                                "date_group": date_group,
                                "start_date": "2022-01-01",
                                "end_date": datetime.date.today().strftime("%Y-%m-%d"),
                                "cost_type": cost_type,
                                "download": download,
                            },
                        )
                        assert response.status_code == 200
                else:
                    response = client.get(
                        reverse("cost_dashboard"),
                        data={
                            "x_axis": x_axis,
                            "date_group": date_group,
                            "download": download,
                        },
                    )
                    assert response.status_code == 200
            if x_axis != "day":
                break


@pytest.mark.django_db
def test_get_manage_pilots(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("manage_pilots"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_get_pilots_form(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("manage_pilots_form"))
    assert response.status_code == 200

    # Test with a pilot_id that doesn't exists
    response = client.get(reverse("manage_pilots_form", kwargs={"pilot_id": 100}))
    assert response.status_code == 404
