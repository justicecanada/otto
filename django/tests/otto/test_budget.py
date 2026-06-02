"""
Test budget-related views and budget_required decorator.
"""

from django.urls import reverse

import pytest

from otto.models import Cost, CostGroup, CostType, cad_cost


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
    user.monthly_budget = 32
    user.save()

    assert user.this_month_max == 32
    # Create a cost object that is under budget
    Cost.objects.create(
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
    Cost.objects.create(
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

    assert user.this_month_max == 132
    assert not user.is_over_budget

    # Try again
    response = client.post(reverse("laws:search"), data=data)
    assert response.status_code == 200


@pytest.mark.django_db
def test_cost_group_over_budget_blocks_request(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    cost_group = CostGroup.objects.create(
        cost_group_id="test-cost-group",
        name="Test Cost Group",
        monthly_max=100,  # CAD
        active=True,
    )
    cost_group.users.add(user)

    session = client.session
    session["selected_cost_group_id"] = cost_group.id
    session.save()

    cost_type = CostType.objects.first()
    Cost.objects.create(
        user=user,
        cost_type=cost_type,
        usd_cost=80.0,  # ≈$110 CAD → over $100 monthly max
        cost_group=cost_group,
    )

    assert cost_group.is_over_budget

    data = {
        "query": "blah",
        "ai_answer": "on",
        "advanced": "false",
    }

    response = client.post(reverse("laws:search"), data=data)
    assert response.status_code == 302
    assert user.notifications.count() == 1

    headers = {
        "HTTP_HX_REQUEST": "true",
        "HTTP_HX_CURRENT_URL": reverse("laws:search"),
    }
    response = client.post(reverse("laws:search"), data=data, **headers)
    assert response.status_code == 200
    assert response["HX-Redirect"] == reverse("laws:search")
    assert user.notifications.count() == 2


@pytest.mark.django_db
def test_project_cost_tracking(client, all_apps_user):
    """
    Test that:
    1. User incurring non-project cost eats into their personal budget
    2. User incurring project-based cost eats into the project budget
    3. There is no overlap/double-charging
    """
    user = all_apps_user()
    client.force_login(user)

    # Set user's personal budget to $50 CAD
    user.monthly_max = 50
    user.save()

    # Create a cost group with a $300 CAD budget
    cost_group = CostGroup.objects.create(
        cost_group_id="test-project-123",
        name="Test Project",
        monthly_max=300,
        active=True,
    )
    cost_group.users.add(user)

    cost_type = CostType.objects.first()

    # Test 1: Non-project cost eats into personal budget only
    # Create a $10 USD cost (≈$13.80 CAD at default exchange rate)
    Cost.objects.create(
        user=user,
        cost_type=cost_type,
        usd_cost=10.0,
        cost_group=None,  # No cost group = personal cost
    )

    # Verify personal budget tracking
    user_cost_this_month = cad_cost(Cost.objects.get_user_cost_this_month(user))
    assert user_cost_this_month > 0, "Personal cost should be tracked"
    assert user_cost_this_month == pytest.approx(13.8, rel=0.01), (
        f"Expected ~$13.80 CAD, got ${user_cost_this_month}"
    )

    # Verify cost group budget is unaffected
    cost_group_cost_this_month = cad_cost(
        Cost.objects.get_cost_group_cost_this_month(cost_group)
    )
    assert cost_group_cost_this_month == 0, (
        "Cost group budget should be zero when no cost group costs incurred"
    )

    # Verify user is not over personal budget yet
    assert not user.is_over_budget, (
        "User should not be over budget with $13.80 of $50 used"
    )

    # Test 2: Cost group cost eats into cost group budget only
    # Create a $100 USD cost assigned to the cost group (≈$138 CAD)
    Cost.objects.create(
        user=user,
        cost_type=cost_type,
        usd_cost=100.0,
        cost_group=cost_group,  # Assigned to cost group
    )

    # Verify cost group budget tracking
    cost_group_cost_this_month = cad_cost(
        Cost.objects.get_cost_group_cost_this_month(cost_group)
    )
    assert cost_group_cost_this_month > 0, "Cost group cost should be tracked"
    assert cost_group_cost_this_month == pytest.approx(138.0, rel=0.01), (
        f"Expected ~$138 CAD, got ${cost_group_cost_this_month}"
    )

    # Test 3: No overlap/double-charging
    # Personal budget should still only show the personal cost, not the cost group cost
    user_cost_this_month = cad_cost(Cost.objects.get_user_cost_this_month(user))
    assert user_cost_this_month == pytest.approx(13.8, rel=0.01), (
        f"Personal budget should still be ~$13.80, got ${user_cost_this_month}. Cost group costs should not count against personal budget."
    )

    # User should still not be over personal budget
    assert not user.is_over_budget, (
        "User should not be over personal budget ($13.80 of $50). Cost group costs should not affect personal budget."
    )

    # Verify cost group is not over budget yet
    assert not cost_group.is_over_budget, (
        "Cost group should not be over budget ($138 of $300 used)"
    )

    # Test edge case: Add more cost group costs to exceed cost group budget
    Cost.objects.create(
        user=user,
        cost_type=cost_type,
        usd_cost=150.0,  # Additional $150 USD (≈$207 CAD) → total cost group cost ≈$345 CAD
        cost_group=cost_group,
    )

    # Cost group should now be over budget
    cost_group_cost_this_month = cad_cost(
        Cost.objects.get_cost_group_cost_this_month(cost_group)
    )
    assert cost_group_cost_this_month == pytest.approx(345.0, rel=0.01), (
        f"Expected ~$345 CAD, got ${cost_group_cost_this_month}"
    )
    assert cost_group.is_over_budget, "Cost group should be over budget ($345 of $300)"

    # But user's personal budget should be unaffected
    user_cost_this_month = cad_cost(Cost.objects.get_user_cost_this_month(user))
    assert user_cost_this_month == pytest.approx(13.8, rel=0.01), (
        f"Personal budget should still be ~$13.80, got ${user_cost_this_month}"
    )
    assert not user.is_over_budget, "User should still not be over personal budget"

    # Test edge case: Add more personal costs to exceed personal budget
    Cost.objects.create(
        user=user,
        cost_type=cost_type,
        usd_cost=30.0,  # Additional $30 USD (≈$41.40 CAD) → total personal cost ≈$55.20 CAD
        cost_group=None,
    )

    # User should now be over personal budget
    user_cost_this_month = cad_cost(Cost.objects.get_user_cost_this_month(user))
    assert user_cost_this_month == pytest.approx(55.2, rel=0.01), (
        f"Expected ~$55.20 CAD, got ${user_cost_this_month}"
    )
    assert user.is_over_budget, "User should be over personal budget ($55.20 of $50)"

    # Cost group budget should be unaffected by personal costs
    cost_group_cost_this_month = cad_cost(
        Cost.objects.get_cost_group_cost_this_month(cost_group)
    )
    assert cost_group_cost_this_month == pytest.approx(345.0, rel=0.01), (
        f"Cost group budget should still be ~$345, got ${cost_group_cost_this_month}"
    )

    # Cleanup
    Cost.objects.filter(user=user).delete()
    cost_group.delete()
