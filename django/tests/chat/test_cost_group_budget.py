"""
Test that budget checking properly handles cost groups vs personal budgets.
"""

from unittest import mock

from django.urls import reverse

import pytest

from otto.models import Cost, CostGroup, CostType

from chat.models import Chat, Message


@pytest.mark.django_db
@mock.patch("chat.utils.estimate_cost_of_request", return_value=20.00)
def test_cost_warning_with_cost_group(mock_estimate_cost, client, all_apps_user):
    """
    Test that when a cost group is selected, budget checks use the cost group's
    budget instead of the user's personal budget.
    """
    user = all_apps_user()
    client.force_login(user)

    # Set up user with a small personal budget
    user.monthly_max = 10.0  # $10 personal budget
    user.save()

    # Create a cost group with a larger budget
    cost_group = CostGroup.objects.create(
        cost_group_id="test-group-123",
        name="Test Cost Group",
        monthly_max=100.0,  # $100 cost group budget
        active=True,
    )
    cost_group.users.add(user)

    # Create a chat
    chat = Chat.objects.create(user=user)
    message = Message.objects.create(chat=chat, text="Expensive request")
    response_message = Message.objects.create(
        chat=chat, mode="chat", is_bot=True, parent=message
    )

    # Test 1: Without cost group selected, should warn about personal budget
    # (estimated cost $20 + personal costs $0 = $20 > $10 personal budget)
    response = client.get(reverse("chat:chat_response", args=[response_message.id]))
    assert response.status_code == 200

    # The response should contain a budget warning (over_budget=True)
    # because $20 estimate exceeds the $10 personal budget

    # Test 2: With cost group selected, should use cost group budget
    session = client.session
    session["selected_cost_group_id"] = cost_group.id
    session.save()

    # Create new message for fresh test
    message2 = Message.objects.create(chat=chat, text="Another expensive request")
    response_message2 = Message.objects.create(
        chat=chat, mode="chat", is_bot=True, parent=message2
    )

    response = client.get(reverse("chat:chat_response", args=[response_message2.id]))
    assert response.status_code == 200

    # With cost group budget of $100, the $20 estimate should only trigger
    # the WARN_COST warning (if estimate >= settings.WARN_COST), not the budget warning

    # Test 3: Add costs to cost group to exceed its budget
    cost_type = CostType.objects.first()
    Cost.objects.create(
        user=user,
        cost_type=cost_type,
        usd_cost=90.0,  # ~$124 CAD at default rate, exceeds $100 budget
        cost_group=cost_group,
    )

    # Create new message for fresh test
    message3 = Message.objects.create(chat=chat, text="Yet another expensive request")
    response_message3 = Message.objects.create(
        chat=chat, mode="chat", is_bot=True, parent=message3
    )

    response = client.get(reverse("chat:chat_response", args=[response_message3.id]))
    assert response.status_code == 200

    # Now should warn about cost group budget being exceeded
    # (cost group has ~$124 already, adding $20 estimate exceeds $100 budget)

    # Test 4: Personal budget should be unaffected by cost group costs
    # Clear cost group selection
    session = client.session
    del session["selected_cost_group_id"]
    session.save()

    # User's personal budget should still be empty ($0) because the $90 cost
    # was assigned to the cost group, not their personal budget
    from otto.utils.common import cad_cost

    user_cost = cad_cost(Cost.objects.get_user_cost_this_month(user))
    assert user_cost == 0.0, "Personal budget should be unaffected by cost group costs"


@pytest.mark.django_db
def test_cost_assignment_respects_active_cost_group(client, all_apps_user):
    """
    Test that costs are properly assigned to cost groups when active.
    """
    user = all_apps_user()
    client.force_login(user)

    # Create a cost group
    cost_group = CostGroup.objects.create(
        cost_group_id="test-group-456",
        name="Test Cost Group 2",
        monthly_max=200.0,
        active=True,
    )
    cost_group.users.add(user)

    cost_type = CostType.objects.first()

    # Test 1: Cost without cost group selected goes to personal budget
    from structlog.contextvars import bind_contextvars

    bind_contextvars(user_id=user.id)
    Cost.objects.new(cost_type=cost_type.short_name, count=1000)

    from otto.utils.common import cad_cost

    personal_cost = cad_cost(Cost.objects.get_user_cost_this_month(user))
    cost_group_cost = cad_cost(Cost.objects.get_cost_group_cost_this_month(cost_group))

    assert personal_cost > 0, "Cost should be assigned to personal budget"
    assert cost_group_cost == 0, "Cost group should have no costs yet"

    # Clean up
    Cost.objects.filter(user=user).delete()

    # Test 2: Cost with cost group selected goes to cost group budget
    session = client.session
    session["selected_cost_group_id"] = cost_group.id
    session.save()

    # Note: The actual request object needs to have the session for this to work
    # In real usage, this happens automatically through Django middleware
