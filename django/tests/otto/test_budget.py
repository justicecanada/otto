"""
Test budget-related views and budget_required decorator.
"""

from django.urls import reverse
from django.utils.translation import gettext as _

import pytest

from otto.models import Cost, CostType


@pytest.mark.django_db
def test_get_user_cost(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    response = client.get(reverse("user_cost"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_exceed_budget(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    user.monthly_budget = 40
    user.save()

    assert user.this_month_max == 40
    # Create a cost object that is under budget
    cost = Cost.objects.create(
        user=user,
        cost_type=CostType.objects.first(),
        usd_cost=1,
    )
    assert not user.is_over_budget

    # POST laws:search URL (which requires user to have remaining budget)
    data = {
        "query": "blah",
        "ai_answer": "on",
        "advanced": "false",
    }
    response = client.post(reverse("laws:search"), data=data)
    assert response.status_code == 200

    # Create a cost object that is over budget
    cost = Cost.objects.create(
        user=user,
        cost_type=CostType.objects.first(),
        usd_cost=50,
    )

    assert user.is_over_budget

    # POST laws:search URL (which requires user to have remaining budget)
    response = client.post(reverse("laws:search"), data=data)
    # It should redirect back home and create a notification
    assert response.status_code == 302
    assert user.notifications.count() == 1

    # Try the same POST but with headers HX-Request = true and HX-Current-URL = the url
    headers = {
        "HTTP_HX_REQUEST": "true",
        "HTTP_HX_CURRENT_URL": reverse("laws:search"),
    }
    response = client.post(reverse("laws:search"), data=data, **headers)
    assert response.status_code == 200
    # In this case it should respond with an HX-Redirect header to the current URL
    assert response["HX-Redirect"] == reverse("laws:search")

    # Add monthly bonus
    user.monthly_bonus = 100
    user.save()

    assert user.this_month_max == 140
    assert not user.is_over_budget

    # Try again
    response = client.post(reverse("laws:search"), data=data)
    assert response.status_code == 200
