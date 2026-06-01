"""
Test that cost group attribution works correctly across all apps.
This verifies that the centralized Cost.objects.new() method correctly
attributes costs to users and cost groups based on user_id and cost_group_id in contextvars.
"""

import pytest
from chat_next.models import Chat as ChatNext
from chat_next.models import Message as MessageNext
from structlog.contextvars import bind_contextvars

from otto.models import Cost, CostGroup


@pytest.mark.django_db
def test_cost_attribution_with_cost_group(all_apps_user):
    """Test that costs are attributed to cost group when user_id and cost_group_id are in context"""
    user = all_apps_user()
    cost_group = CostGroup.objects.create(
        cost_group_id="TEST001",
        name="Test Project",
        monthly_max=1000,
        active=True,
    )

    # Bind user_id and cost_group_id to contextvars (like views do)
    bind_contextvars(user_id=user.id, cost_group_id=cost_group.id, feature="chat")

    # Create a cost
    cost = Cost.objects.new(cost_type="gpt-4.1-in", count=100)

    # Verify cost is attributed to both user and cost group
    assert cost.user == user
    assert cost.cost_group == cost_group
    assert cost.feature == "chat"


@pytest.mark.django_db
def test_cost_attribution_without_cost_group(all_apps_user):
    """Test that costs are attributed to user only when no cost group is in context"""
    user = all_apps_user()

    # Bind only user_id to contextvars (no cost_group_id)
    bind_contextvars(user_id=user.id, feature="chat")

    # Create a cost
    cost = Cost.objects.new(cost_type="gpt-4.1-in", count=100)

    # Verify cost is attributed to user only
    assert cost.user == user
    assert cost.cost_group is None
    assert cost.feature == "chat"


@pytest.mark.django_db
def test_cost_attribution_laws_loading_never_has_user():
    """Test that laws_load feature uses Otto admin cost group, and has no user when no user_id in context"""
    from otto.models import CostGroup

    # Ensure Otto admin cost group exists
    otto_admin = CostGroup.objects.get_or_create(
        cost_group_id="otto-admin",
        defaults={"name": "Otto administration", "monthly_max": 999999, "active": True},
    )[0]

    # Bind only feature (no user_id or cost_group_id)
    bind_contextvars(feature="laws_load")

    # Create a cost
    cost = Cost.objects.new(cost_type="embedding", count=1000)

    # Verify cost has no user but is attributed to Otto admin cost group
    assert cost.user is None
    assert cost.cost_group == otto_admin
    assert cost.feature == "laws_load"


@pytest.mark.django_db
def test_cost_attribution_laws_loading_even_with_user_in_context(all_apps_user):
    """Test that laws_load feature tracks user but ignores cost_group from context and uses Otto admin"""
    user = all_apps_user()
    cost_group = CostGroup.objects.create(
        cost_group_id="TEST001",
        name="Test Project",
        monthly_max=1000,
        active=True,
    )

    # Ensure Otto admin cost group exists
    otto_admin = CostGroup.objects.get_or_create(
        cost_group_id="otto-admin",
        defaults={"name": "Otto administration", "monthly_max": 999999, "active": True},
    )[0]

    # Bind user_id and cost_group_id with laws_load feature
    # This simulates a user initiating laws loading from the UI
    bind_contextvars(user_id=user.id, cost_group_id=cost_group.id, feature="laws_load")

    # Create a cost
    cost = Cost.objects.new(cost_type="embedding", count=1000)

    # Verify cost tracks the user who initiated it, but uses Otto admin cost group (not the one from context)
    assert cost.user == user  # User is tracked
    assert cost.cost_group == otto_admin
    assert cost.cost_group != cost_group  # Explicitly not the context cost group
    assert cost.feature == "laws_load"


@pytest.mark.django_db
def test_cost_attribution_librarian(all_apps_user):
    """Test that librarian costs are attributed correctly"""
    user = all_apps_user()
    cost_group = CostGroup.objects.create(
        cost_group_id="TEST001",
        name="Test Project",
        monthly_max=1000,
        active=True,
    )

    # Bind user_id and cost_group_id (like librarian tasks do)
    bind_contextvars(user_id=user.id, cost_group_id=cost_group.id, feature="librarian")

    # Create a cost (simulating document processing)
    cost = Cost.objects.new(cost_type="embedding", count=500)

    # Verify attribution
    assert cost.user == user
    assert cost.cost_group == cost_group
    assert cost.feature == "librarian"


@pytest.mark.django_db
def test_cost_attribution_text_extractor(all_apps_user):
    """Test that text_extractor costs are attributed correctly"""
    user = all_apps_user()

    # Bind only user_id (no cost_group_id)
    bind_contextvars(user_id=user.id, feature="text_extractor")

    # Create a cost (simulating OCR)
    cost = Cost.objects.new(cost_type="doc-ai-read", count=10)

    # Verify attribution
    assert cost.user == user
    assert cost.cost_group is None
    assert cost.feature == "text_extractor"


@pytest.mark.django_db
def test_cost_attribution_no_user_context():
    """Test cost creation when there's no user_id or cost_group_id in context"""
    # Bind only feature (no user_id or cost_group_id)
    bind_contextvars(feature="chat")

    # Create a cost
    cost = Cost.objects.new(cost_type="gpt-4.1-in", count=100)

    # Verify no user or cost group attribution
    assert cost.user is None
    assert cost.cost_group is None
    assert cost.feature == "chat"


@pytest.mark.django_db
def test_cost_attribution_with_message_next_id(all_apps_user):
    """Test that costs are linked to chat_next.Message and trigger message cost recalculation."""
    user = all_apps_user()
    chat = ChatNext.objects.create(user=user, title="Cost test")
    message_next = MessageNext.objects.create(chat=chat, text="hello", is_bot=True)

    bind_contextvars(
        user_id=user.id,
        feature="chat",
        message_next_id=message_next.id,
    )

    cost = Cost.objects.new(cost_type="gpt-4.1-in", count=100)

    assert cost.message_next_id == message_next.id
    message_next.refresh_from_db()
    assert message_next.usd_cost == cost.usd_cost
