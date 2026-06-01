from django.urls import reverse

import pytest

from otto.models import CostGroup, Group, User


@pytest.mark.django_db
def test_modify_user_cost_groups(client, all_apps_user):
    admin_user = all_apps_user()
    client.force_login(admin_user)

    # Create a user to modify
    user = User.objects.create_user(upn="test_user", email="test@example.com")

    # Create some cost groups
    cg1 = CostGroup.objects.create(cost_group_id="cg1", name="Cost Group 1")
    cg2 = CostGroup.objects.create(cost_group_id="cg2", name="Cost Group 2")

    # Create a group (role)
    group = Group.objects.create(name="Test Role")

    # Modify the user to add cost groups
    response = client.post(
        reverse("manage_users"),
        data={
            "upn": [user.id],
            "group": [group.id],
            "cost_group": [cg1.id, cg2.id],
            "monthly_max": 100,
            "monthly_bonus": 0,
        },
    )

    assert response.status_code == 200

    user.refresh_from_db()
    assert user.groups.count() == 1
    assert user.available_cost_groups.count() == 2
    assert cg1 in user.available_cost_groups.all()
    assert cg2 in user.available_cost_groups.all()

    # Now remove one cost group
    response = client.post(
        reverse("manage_users"),
        data={
            "upn": [user.id],
            "group": [group.id],
            "cost_group": [cg1.id],
            "monthly_max": 100,
            "monthly_bonus": 0,
        },
    )

    assert response.status_code == 200

    user.refresh_from_db()
    assert user.available_cost_groups.count() == 1
    assert cg1 in user.available_cost_groups.all()
    assert cg2 not in user.available_cost_groups.all()
