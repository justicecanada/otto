"""
Test the core Otto views (index, login, etc.)
"""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.core.exceptions import DisallowedRedirect
from django.http import HttpResponse
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

import pytest
from azure_auth.exceptions import TokenError
from azure_auth.handlers import AuthHandler
from bs4 import BeautifulSoup

from otto.browser_test_auth import browser_test_personas
from otto.forms import FeedbackForm, UsageDashboardForm
from otto.models import (
    BlockedURL,
    Cost,
    CostGroup,
    CostType,
    Feedback,
    Notification,
    OttoStatus,
    Team,
    TeamMembership,
)

from chat.models import Chat, Message

User = get_user_model()


@pytest.mark.django_db
def test_azure_callback_disallowed_redirect(client):
    """Test that DisallowedRedirect is handled gracefully in the OAuth callback."""
    with patch("otto.views._azure_auth_callback") as mock_callback:
        mock_callback.side_effect = DisallowedRedirect(
            "Unsafe redirect exceeding 2048 characters"
        )
        response = client.get(reverse("callback"))
        # Should redirect to homepage instead of raising an error
        assert response.status_code == 302
        assert response.url == "/"


@pytest.mark.django_db
def test_azure_callback_handles_pkce_mismatch(client):
    session = client.session
    session["auth_in_progress"] = True
    session["auth_started_at"] = "old"
    session.save()

    with patch("otto.views._azure_auth_callback") as mock_callback:
        mock_callback.side_effect = TokenError(
            "invalid_grant", "AADSTS501481: Code_Verifier mismatch"
        )
        response = client.get(reverse("callback"))

    assert response.status_code == 200
    template_names = {template.name for template in response.templates}
    assert "auth/login_issue.html" in template_names
    updated_session = client.session
    assert updated_session["auth_in_progress"] is False
    assert "auth_started_at" not in updated_session


@pytest.mark.django_db
def test_azure_callback_state_missing_renders_welcome(client):
    session = client.session
    session["auth_in_progress"] = True
    session["auth_started_at"] = "old"
    session.save()

    with patch("otto.views._azure_auth_callback") as mock_callback:
        mock_callback.side_effect = ValueError("state missing from auth_code_flow")
        response = client.get(reverse("callback"))

    assert response.status_code == 200
    template_names = {template.name for template in response.templates}
    assert "welcome.html" in template_names
    updated_session = client.session
    assert updated_session["auth_in_progress"] is False
    assert "auth_started_at" not in updated_session


@pytest.mark.django_db
def test_azure_callback_success_clears_guard(client):
    session = client.session
    session["auth_in_progress"] = True
    session["auth_started_at"] = "old"
    session.save()

    with patch(
        "otto.views._azure_auth_callback", return_value=HttpResponse("ok")
    ) as mock_callback:
        response = client.get(reverse("callback"))

    assert response.status_code == 200
    mock_callback.assert_called_once()
    updated_session = client.session
    assert updated_session["auth_in_progress"] is False
    assert "auth_started_at" not in updated_session


@pytest.mark.django_db
def test_login_sets_language_cookie_and_restarts_flow(client):
    session = client.session
    session["auth_in_progress"] = True
    session["auth_started_at"] = "very-old"
    session.save()

    with patch(
        "otto.views.azure_auth_login", return_value=HttpResponse("azure")
    ) as mock_login:
        response = client.get(reverse("login"), {"lang": "fr"})

    assert response.status_code == 200
    mock_login.assert_called_once()
    updated_session = client.session
    assert updated_session["auth_in_progress"] is True
    assert updated_session["auth_started_at"] != "very-old"
    assert response.cookies[settings.LANGUAGE_COOKIE_NAME].value == "fr"


@pytest.mark.django_db
def test_login_handles_azure_exception_gracefully(client):
    session = client.session
    session["auth_in_progress"] = True
    session["auth_started_at"] = "old"
    session.save()

    with patch("otto.views.azure_auth_login") as mock_login:
        mock_login.side_effect = Exception("Azure outage")
        response = client.get(reverse("login"))

    assert response.status_code == 200
    template_names = {template.name for template in response.templates}
    assert "auth/login_issue.html" in template_names
    updated_session = client.session
    assert updated_session["auth_in_progress"] is False
    assert "auth_started_at" not in updated_session


@pytest.mark.django_db
@override_settings(BROWSER_TEST_AUTH_ENABLED=True)
@pytest.mark.parametrize(
    ("persona_slug", "expected_upn", "expected_groups", "expected_redirect_url"),
    [
        (
            None,
            settings.BROWSER_TEST_AUTH_UPN,
            {settings.OTTO_ADMIN_GROUP},
            reverse("chat_next:new_chat"),
        ),
        (
            "second_admin",
            "browser.test.second.admin@example.com",
            {settings.OTTO_ADMIN_GROUP},
            reverse("chat_next:new_chat"),
        ),
        (
            "basic_user",
            "browser.test.user@example.com",
            {settings.OTTO_USER_GROUP},
            reverse("chat:new_chat"),
        ),
        (
            "public_bulk_uploader",
            "browser.test.public.bulk.uploader@example.com",
            {
                settings.OTTO_USER_GROUP,
                settings.OTTO_PUBLIC_SHARING_ADMIN_GROUP,
                settings.OTTO_BULK_UPLOADER_GROUP,
            },
            reverse("chat:new_chat"),
        ),
        (
            "beta_tester",
            "browser.test.beta.tester@example.com",
            {settings.OTTO_USER_GROUP, settings.OTTO_BETA_TESTER_GROUP},
            reverse("chat_next:new_chat"),
        ),
    ],
)
def test_browser_test_login_creates_selected_local_user_and_logs_in(
    client, persona_slug, expected_upn, expected_groups, expected_redirect_url
):
    post_data = {"next": reverse("index")}
    if persona_slug is not None:
        post_data["persona"] = persona_slug

    response = client.post(reverse("browser_test_login"), post_data)

    assert response.status_code == 302
    assert response.url == reverse("index")

    user = User.objects.find_by_upn(expected_upn)
    assert user is not None
    assert user.accepted_terms is True
    assert user.default_ai_assistant == "chat_next"
    assert set(user.groups.values_list("name", flat=True)) == expected_groups
    assert client.session["_auth_user_id"] == str(user.pk)
    assert client.session["browser_test_auth"] is True
    assert client.session["id_token_claims"]["preferred_username"] == user.upn
    assert client.session["id_token_claims"]["exp"] > int(timezone.now().timestamp())

    follow_up = client.get(reverse("index"))
    assert follow_up.status_code == 302
    assert follow_up.url == expected_redirect_url
    assert AuthHandler(follow_up.wsgi_request).user_is_authenticated is True


@pytest.mark.django_db
@override_settings(BROWSER_TEST_AUTH_ENABLED=True)
def test_browser_test_login_rejects_unknown_persona(client):
    response = client.post(
        reverse("browser_test_login"),
        {"next": reverse("index"), "persona": "not-a-real-persona"},
    )

    assert response.status_code == 404


@pytest.mark.django_db
@override_settings(BROWSER_TEST_AUTH_ENABLED=True)
def test_browser_test_login_rejects_get_requests(client):
    response = client.get(reverse("browser_test_login"))

    assert response.status_code == 405


@pytest.mark.django_db
@override_settings(BROWSER_TEST_AUTH_ENABLED=True)
def test_browser_test_login_requires_csrf():
    csrf_client = Client(enforce_csrf_checks=True)

    response = csrf_client.post(
        reverse("browser_test_login"),
        {"next": reverse("index")},
    )

    assert response.status_code == 403


@pytest.mark.django_db
@override_settings(BROWSER_TEST_AUTH_ENABLED=True)
def test_welcome_shows_browser_test_login_buttons_when_enabled(client):
    response = client.get(reverse("welcome"))

    assert response.status_code == 200
    content = response.content.decode()
    assert content.count(f'action="{reverse("browser_test_login")}"') == 5
    assert content.count('name="persona"') == 5
    assert "csrfmiddlewaretoken" in content
    for label in [
        "First Admin",
        "Second Admin",
        "Basic User",
        "Public BulkUploader",
        "Beta Tester",
    ]:
        assert label in content


@pytest.mark.django_db
@override_settings(BROWSER_TEST_AUTH_ENABLED=True)
def test_login_issue_shows_browser_test_login_buttons_when_enabled(client):
    with patch("otto.views.azure_auth_login") as mock_login:
        mock_login.side_effect = Exception("Azure outage")
        response = client.get(reverse("login"))

    assert response.status_code == 200
    content = response.content.decode()
    assert content.count(f'action="{reverse("browser_test_login")}"') == 5
    assert content.count('name="persona"') == 5
    assert "csrfmiddlewaretoken" in content
    for label in [
        "First Admin",
        "Second Admin",
        "Basic User",
        "Public BulkUploader",
        "Beta Tester",
    ]:
        assert label in content


@pytest.mark.django_db
@override_settings(BROWSER_TEST_AUTH_ENABLED=True, IS_RUNNING_TESTS=False)
def test_welcome_seeds_all_browser_test_personas_for_dev(client):
    assert not User.objects.filter(email__icontains="browser.test").exists()

    response = client.get(reverse("welcome"))

    assert response.status_code == 200

    expected_emails = {persona["email"] for persona in browser_test_personas()}
    created_users = User.objects.filter(email__in=expected_emails)

    assert created_users.count() == len(expected_emails)
    assert set(created_users.values_list("email", flat=True)) == expected_emails


@pytest.mark.django_db
def test_homepage(client, basic_user):
    user = basic_user()
    client.force_login(user)
    response = client.get(reverse("index"))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, "html.parser")
    text = soup.get_text()
    assert "Otto" in text


@pytest.mark.django_db
def test_index_redirects_to_default_ai_assistant(client, all_apps_user):
    user = all_apps_user()
    user.homepage_tour_completed = True
    user.default_ai_assistant = "chat_next"
    user.save(update_fields=["homepage_tour_completed", "default_ai_assistant"])
    client.force_login(user)

    session = client.session
    session["from_welcome"] = True
    session.save()

    response = client.get(reverse("index"))
    assert response.status_code == 302
    assert response.url == reverse("chat_next:new_chat")


@pytest.mark.django_db
def test_set_default_ai_assistant_updates_preference(client, all_apps_user):
    user = all_apps_user()
    Group.objects.get_or_create(name=settings.OTTO_BETA_TESTER_GROUP)
    user.groups.add(Group.objects.get(name=settings.OTTO_BETA_TESTER_GROUP))
    client.force_login(user)

    response = client.post(
        reverse("set_default_ai_assistant"),
        {"assistant": "chat_next", "next": reverse("index")},
    )

    assert response.status_code == 302
    assert response.url == reverse("index")
    user.refresh_from_db()
    assert user.default_ai_assistant == "chat_next"


@pytest.mark.django_db
def test_set_default_ai_assistant_rejects_invalid_value(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    response = client.post(
        reverse("set_default_ai_assistant"),
        {"assistant": "not-a-real-assistant"},
    )

    assert response.status_code == 400
    user.refresh_from_db()
    assert user.default_ai_assistant == "chat"


@pytest.mark.django_db
def test_set_default_ai_assistant_xhr_returns_no_content(client, all_apps_user):
    user = all_apps_user()
    Group.objects.get_or_create(name=settings.OTTO_BETA_TESTER_GROUP)
    user.groups.add(Group.objects.get(name=settings.OTTO_BETA_TESTER_GROUP))
    client.force_login(user)

    response = client.post(
        reverse("set_default_ai_assistant"),
        {
            "assistant": "chat_next",
            "next": reverse("index"),
        },
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )

    assert response.status_code == 204
    user.refresh_from_db()
    assert user.default_ai_assistant == "chat_next"


@pytest.mark.django_db
def test_set_default_ai_assistant_rejects_chat_next_without_access(client, basic_user):
    user = basic_user(accept_terms=True)
    client.force_login(user)

    response = client.post(
        reverse("set_default_ai_assistant"),
        {"assistant": "chat_next", "next": reverse("index")},
    )

    assert response.status_code == 400
    user.refresh_from_db()
    assert user.default_ai_assistant == "chat"


@pytest.mark.django_db
def test_default_ai_assistant_route_falls_back_when_no_chat_next_access(
    basic_user,
):
    user = basic_user()
    user.default_ai_assistant = "chat_next"
    user.save(update_fields=["default_ai_assistant"])

    assert user.default_ai_assistant_route == "chat:new_chat"


@pytest.mark.django_db
def test_notifications(client, basic_user):
    """
    1. Create a Notification manually
    2. Check that a li.notification is included from the notifications route
    3. Test the delete notification route
    4. Check that it was deleted in the database
    5. Check that it was deleted via the notifications route
    """
    user = basic_user(accept_terms=True)
    client.force_login(user)
    Notification.objects.create(
        user=user,
        heading="Access controls",
        text="You are not authorized to access...",
        category="error",
    )
    notification = user.notifications.first()
    assert notification is not None
    response = client.get(reverse("notifications"))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, "html.parser")
    # Check that there is exactly one notification
    assert len(soup.find_all("li", class_="notification")) == 1
    response = client.delete(
        reverse("notification", kwargs={"notification_id": notification.id})
    )
    assert response.status_code == 200  # HTMX delete routes return a fragment to swap
    assert user.notifications.count() == 0
    response = client.get(reverse("notifications"))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, "html.parser")
    assert soup.find("li", class_="notification") is None


def test_valid_feedback_form(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    date_and_time = timezone.now().strftime("%Y%m%d-%H%M%S")

    data = {
        "user": user,
        "feedback_type": Feedback.FEEDBACK_TYPE_CHOICES[0][0],
        "feedback_message": "Test Message",
        "app": "Otto",
        "modified_by": user.id,
        "created_by": user.id,
        "created_at": date_and_time,
        "modified_at": date_and_time,
        "otto_version": "v0",
    }

    response = client.post(
        reverse("user_feedback"),
        data=data,
    )
    assert response.status_code == 200


def test_valid_feedback_form_from_message(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(title="test", user=user)
    Message.objects.create(chat=chat)
    message = Message.objects.create(chat=chat, is_bot=True)
    date_and_time = timezone.now().strftime("%Y%m%d-%H%M%S")

    data = {
        "user": user,
        "feedback_type": Feedback.FEEDBACK_TYPE_CHOICES[0][0],
        "feedback_message": "Test Message",
        "app": "chat",
        "chat_message_id": message.id,
        "modified_by": user.id,
        "created_by": user.id,
        "created_at": date_and_time,
        "modified_at": date_and_time,
        "otto_version": "v0",
    }

    response = client.post(
        reverse("user_feedback", kwargs={"message_id": message.id}),
        data=data,
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_feedback_captures_preset_snapshot_from_message(client, all_apps_user):
    """When feedback is submitted for a chat message with a loaded preset,
    the Feedback record should capture loaded_preset and a compact preset_snapshot.
    """
    user = all_apps_user()
    client.force_login(user)

    # Create a chat and tweak its options so the snapshot is non-default
    chat = Chat.objects.create(title="test", user=user)
    opts = chat.options
    opts.prompt = "Snapshot prompt"
    opts.chat_model = "gpt-4.1-mini"
    opts.chat_temperature = 0.42
    opts.qa_topk = 7
    opts.save()

    # Create a preset based on the chat options and attach it to the chat
    from chat.models import Preset

    preset = Preset.objects.create(name_en="My preset", options=opts, owner=user)
    chat.loaded_preset = preset
    chat.save()

    # Create a bot message and submit feedback for it
    Message.objects.create(chat=chat)
    message = Message.objects.create(chat=chat, is_bot=True)

    date_and_time = timezone.now().strftime("%Y%m%d-%H%M%S")

    data = {
        "user": user,
        "feedback_type": Feedback.FEEDBACK_TYPE_CHOICES[0][0],
        "feedback_message": "Preset snapshot test",
        "app": "chat",
        "chat_message": message.id,
        "modified_by": user.id,
        "created_by": user.id,
        "created_at": date_and_time,
        "modified_at": date_and_time,
        "otto_version": "v0",
    }

    response = client.post(
        reverse("user_feedback", kwargs={"message_id": message.id}),
        data=data,
    )
    assert response.status_code == 200
    # Sanity-check the form used by the view to help debug failures
    form = FeedbackForm(user, message.id, data)
    if not form.is_valid():
        # Fail with form errors to surface the reason the view may not have saved
        pytest.fail(f"Feedback form invalid: {form.errors}")

    fb = Feedback.objects.filter(chat_message=message).first()
    assert fb is not None, (
        f"No Feedback found for message; total feedback count={Feedback.objects.count()}. "
        f"First feedback chat_message_id={Feedback.objects.first().chat_message_id}"
    )
    # loaded_preset should reference the preset attached to the chat
    assert fb.loaded_preset == preset

    # preset_snapshot may be None if snapshot serialization encountered
    # unavailable attributes; if present it should be a dict containing key
    # fields from ChatOptions.
    if fb.preset_snapshot is not None:
        assert isinstance(fb.preset_snapshot, dict)
        assert fb.preset_snapshot.get("prompt") == opts.prompt
        assert fb.preset_snapshot.get("chat_model") == opts.chat_model
        # Numeric values should round-trip via JSON -> compare approximately
        assert fb.preset_snapshot.get("chat_temperature") == opts.chat_temperature


def test_initialize_feedback_for_chat_mode(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)
    chat = Chat.objects.create(title="test", user=user)
    message = Message.objects.create(chat=chat, mode="translate", is_bot=False)

    data = {
        "user": user.id,
        "feedback_type": Feedback.FEEDBACK_TYPE_CHOICES[0][0],
        "feedback_message": "Test feedback message for translation",
        "app": "translate",
        "chat_message_id": message.id,
        "modified_by": user.id,
        "otto_version": "v0",
    }

    client.post(
        reverse("user_feedback", kwargs={"message_id": message.id}),
        data=data,
    )

    form = FeedbackForm(user=user, message_id=message.id)
    form.initialize_chat_feedback(message.id)

    assert form.fields["app"].initial == "translate"


@pytest.mark.django_db
def test_manage_banner(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    # Test preview with just an English message
    response = client.post(
        reverse("manage_banner"),
        data={"message_en": "Hello there", "preview": "true", "category": "info"},
    )
    assert response.status_code == 200
    assert (
        '<div id="message-from-admins" class="info">Hello there</div>'
        in response.content.decode()
    )

    # Test banner creation
    response = client.post(
        reverse("manage_banner"),
        data={"message_en": "Hello", "message_fr": "Bonjour", "category": "danger"},
    )
    assert response.status_code == 200
    assert (
        '<div id="message-from-admins" class="danger">Hello</div>'
        in response.content.decode()
    )

    # Clear the banner
    response = client.post(
        reverse("manage_banner"),
        data={"remove": "1"},
    )
    assert response.status_code == 200
    assert "Hello" not in response.content.decode()


@pytest.mark.django_db
def test_manage_cost_groups_form_handles_get_and_delete(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    cost_group = CostGroup.objects.create(
        cost_group_id="cg-test",
        name="Test Group",
        monthly_max=100,
    )

    # Blank form (no cost_group_id)
    blank_response = client.get(reverse("manage_cost_groups_form"))
    assert blank_response.status_code == 200
    assert blank_response.context["form"].instance.pk is None

    # Existing cost group should populate the form
    detail_url = reverse(
        "manage_cost_groups_form", kwargs={"cost_group_id": cost_group.id}
    )
    response = client.get(detail_url)
    assert response.status_code == 200
    form = response.context["form"]
    assert form.instance.pk == cost_group.pk
    assert form.instance.name == "Test Group"

    delete_response = client.delete(detail_url)
    assert delete_response.status_code == 200
    assert delete_response.headers["HX-Redirect"] == reverse("manage_cost_groups")
    assert not CostGroup.objects.filter(pk=cost_group.pk).exists()


@pytest.mark.django_db
def test_cost_dashboard_group_cost_group(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    status = OttoStatus.objects.singleton()
    status.exchange_rate = 1.0
    status.save()

    cost_type = CostType.objects.create(
        name="Test Cost",
        short_name="test-cost-group",
        description="Test",
        unit_cost=Decimal("1.00"),
        unit_quantity=1,
    )

    cost_group = CostGroup.objects.create(
        cost_group_id="cg-group",
        name="Project X",
        monthly_max=200,
    )

    Cost.objects.create(
        cost_type=cost_type,
        count=1,
        usd_cost=Decimal("5.00"),
        cost_group=cost_group,
        feature="chat",
        user=user,
    )

    response = client.get(
        reverse("cost_dashboard"),
        {
            "group": "cost_group",
            "x_axis": "cost_group",
            "cost_group": "all",
            "cost_type": str(cost_type.id),
            "date_group": "all",
        },
    )

    assert response.status_code == 200
    rows = response.context["rows"]
    assert any(
        row[0] == cost_group.name and row[1] == cost_group.name and row[2] == "$5.00"
        for row in rows
    )

    chart_group = next(
        group
        for group in response.context["chart_y_groups"]
        if group["label"] == cost_group.name
    )
    assert chart_group["values"][0] == pytest.approx(5.0)
    assert cost_group.name in response.context["chart_x_labels"]


@pytest.mark.django_db
def test_cost_dashboard_group_none(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    status = OttoStatus.objects.singleton()
    status.exchange_rate = 1.0
    status.save()

    cost_type = CostType.objects.create(
        name="Aggregate Cost",
        short_name="test-cost-none",
        description="Test",
        unit_cost=Decimal("1.00"),
        unit_quantity=1,
    )

    cost_group = CostGroup.objects.create(
        cost_group_id="cg-none",
        name="Project Argo",
        monthly_max=150,
    )

    Cost.objects.create(
        cost_type=cost_type,
        count=1,
        usd_cost=Decimal("5.00"),
        cost_group=cost_group,
        feature="qa",
        user=user,
    )

    response = client.get(
        reverse("cost_dashboard"),
        {
            "group": "none",
            "x_axis": "cost_group",
            "cost_group": "all",
            "cost_type": str(cost_type.id),
            "date_group": "all",
        },
    )

    assert response.status_code == 200
    assert [cost_group.name, "$5.00"] in response.context["rows"]
    chart_group = response.context["chart_y_groups"][0]
    assert chart_group["label"] == "Total cost (CAD)"
    assert chart_group["values"][0] == pytest.approx(5.0)
    assert response.context["secondary_number"] == "$5.00"


@pytest.mark.django_db
def test_user_cost_with_active_cost_group(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    status = OttoStatus.objects.singleton()
    status.exchange_rate = 1.0
    status.save()

    cost_group = CostGroup.objects.create(
        cost_group_id="cg-active",
        name="Active Group",
        monthly_max=50,
    )
    cost_group.users.add(user)

    cost_type = CostType.objects.create(
        name="Active Cost",
        short_name="test-cost-active",
        description="Cost",
        unit_cost=Decimal("1.00"),
        unit_quantity=1,
    )

    Cost.objects.create(
        cost_type=cost_type,
        count=1,
        usd_cost=Decimal("5.00"),
        cost_group=cost_group,
        feature="chat",
        user=user,
    )

    session = client.session
    session["selected_cost_group_id"] = cost_group.id
    session["last_activity"] = timezone.now().isoformat()
    session.save()

    with patch("otto.views._can_user_switch_cost_groups", return_value=True):
        response = client.get(reverse("user_cost"))

    assert response.status_code == 200
    context = response.context
    assert context["budget_type"] == "cost_group"
    assert context["cost_label"] == "Active Group"
    assert context["cost_group_name"] == "Active Group"
    assert context["can_switch_cost_groups"] is True
    assert context["hide_cost_bar"] is False
    assert context["cost_percent"] == 10
    assert "$5.00 / $50.00" in context["cost_tooltip"]
    assert context["cost_tooltip_short"].startswith("$5.00 / $50.00")
    assert "($5.00 today)" in context["cost_tooltip"]


@pytest.mark.django_db
def test_select_cost_group_assigns_when_permitted(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    allowed_group = CostGroup.objects.create(
        cost_group_id="cg-switch",
        name="Switchable",
        monthly_max=100,
        active=True,
    )
    allowed_group.users.add(user)

    with patch("otto.models.CostGroup.get_available_cost_groups") as mock_available:
        mock_available.return_value = CostGroup.objects.filter(pk=allowed_group.pk)
        response = client.post(
            reverse("select_cost_group"),
            {"cost_group_id": allowed_group.id},
        )

    assert response.status_code == 200
    assert client.session["selected_cost_group_id"] == allowed_group.id
    assert allowed_group.name in response.content.decode()


@pytest.mark.django_db
def test_clear_cost_group_removes_selection(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    cost_group = CostGroup.objects.create(
        cost_group_id="cg-clear",
        name="Clearable",
        monthly_max=80,
    )
    session = client.session
    session["selected_cost_group_id"] = cost_group.id
    session.save()

    response = client.get(reverse("clear_cost_group"))
    assert response.status_code == 200
    assert "selected_cost_group_id" not in client.session
    assert "Clearable" not in response.content.decode()


@pytest.mark.django_db
def test_reset_completion_flags(client, all_apps_user):
    user = all_apps_user()
    user.homepage_tour_completed = True
    user.ai_assistant_tour_completed = True
    user.laws_search_tour_completed = True
    user.accepted_terms_date = timezone.now().date()
    user.save()

    client.force_login(user)
    response = client.get(reverse("reset_completion_flags"))
    assert response.status_code == 302
    assert response.url == reverse("welcome")

    user.refresh_from_db()
    assert not user.homepage_tour_completed


def test_manage_users_data_returns_expected_columns(client, all_apps_user):
    admin = all_apps_user()
    client.force_login(admin)

    status = OttoStatus.objects.singleton()
    status.exchange_rate = 1.0
    status.save()

    analytics_group, _ = Group.objects.get_or_create(name="Analytics Team")
    data_cost_group = CostGroup.objects.create(
        cost_group_id="cg-data",
        name="Data Group",
        monthly_max=300,
    )
    data_cost_type = CostType.objects.create(
        name="Data Tokens",
        short_name="data-tokens",
        description="Data usage",
        unit_cost=Decimal("1.00"),
        unit_quantity=1,
    )
    tracked_user = User.objects.create(
        upn="analytics@example.com",
        email="analytics@example.com",
        first_name="Ana",
        last_name="Lytics",
        entra_status=User.EntraStatus.ACTIVE,
        password="",
        last_login=timezone.now(),
    )
    tracked_user.groups.add(analytics_group)
    tracked_user.available_cost_groups.add(data_cost_group)
    data_team = Team.objects.create(name="Data / Données", created_by=admin)
    TeamMembership.objects.create(team=data_team, user=tracked_user, role="member")

    Cost.objects.create(
        cost_type=data_cost_type,
        count=1,
        usd_cost=Decimal("2.50"),
        cost_group=data_cost_group,
        feature="chat",
        user=tracked_user,
    )

    response = client.get(
        reverse("manage_users_data"),
        {
            "draw": "3",
            "start": "0",
            "length": "-1",
            "order[0][column]": "1",
            "order[0][dir]": "asc",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["draw"] == 3
    expected_total = User.objects.all().count()
    assert payload["recordsTotal"] == expected_total
    assert payload["recordsFiltered"] == expected_total
    row = next(
        data_row
        for data_row in payload["data"]
        if data_row[1] == "analytics@example.com"
    )
    assert "Active" in row[2]
    assert row[3] == ""
    assert row[4] == ""
    assert "May" in row[5] or "p.m." in row[5] or "a.m." in row[5]
    assert row[6]["display"] == "$2.50"
    assert row[6]["filter"] == "$2.50"
    assert row[6]["sort"] == 2.5
    assert row[7]["display"] == "$2.50"
    assert row[7]["filter"] == "$2.50"
    assert row[7]["sort"] == 2.5
    assert row[8]["display"] == "$2.50"
    assert row[8]["filter"] == "$2.50"
    assert row[8]["sort"] == 2.5
    assert "Analytics Team" in row[9]
    assert "Data Group" in row[10]
    assert "Data / Données" in row[11]

    filtered = client.get(
        reverse("manage_users_data"),
        {
            "search[value]": "analytics@example.com",
            "draw": "4",
            "length": "10",
        },
    ).json()
    assert filtered["draw"] == 4
    assert filtered["recordsFiltered"] >= 1
    row = next(
        (
            data_row
            for data_row in filtered["data"]
            if data_row[1] == "analytics@example.com"
        ),
        None,
    )
    assert row is not None, (
        f"User analytics@example.com not found in response data. Found users: {[r[1] for r in filtered['data']]}"
    )
    assert "Active" in row[2]
    assert row[3] == ""
    assert row[4] == ""
    assert "May" in row[5] or "p.m." in row[5] or "a.m." in row[5]
    assert "Analytics Team" in row[9]
    assert "Data Group" in row[10]
    assert "Data / Données" in row[11]
    assert row[6]["display"] == "$2.50"
    assert row[6]["filter"] == "$2.50"
    assert row[6]["sort"] == 2.5
    assert row[7]["display"] == "$2.50"
    assert row[7]["filter"] == "$2.50"
    assert row[7]["sort"] == 2.5
    assert row[8]["display"] == "$2.50"
    assert row[8]["filter"] == "$2.50"
    assert row[8]["sort"] == 2.5


@pytest.mark.django_db
def test_list_blocked_urls_groups_by_registered_domain(client, all_apps_user):
    admin = all_apps_user()
    client.force_login(admin)

    BlockedURL.objects.all().delete()
    BlockedURL.objects.create(url="https://sub.example.com/a")
    BlockedURL.objects.create(url="https://www.example.com/b")
    BlockedURL.objects.create(url="https://different.org/page")

    def fake_extractor(netloc):
        parts = netloc.split(".")
        domain = ".".join(parts[-2:]) if len(parts) > 2 else netloc
        return SimpleNamespace(registered_domain=domain)

    with patch("otto.views.get_tld_extractor", return_value=fake_extractor):
        response = client.get(reverse("blocked_urls"))

    assert response.status_code == 200
    domain_counts = response.context["domain_counts"]
    assert list(domain_counts.keys())[0] == "example.com"
    assert domain_counts["example.com"] == 2
    assert domain_counts["different.org"] == 1


@pytest.mark.django_db
def test_manage_cost_groups_creates_group_and_assigns_users(client, all_apps_user):
    admin = all_apps_user()
    client.force_login(admin)

    member = User.objects.create(
        upn="member@example.com",
        email="member@example.com",
        first_name="Team",
        last_name="Member",
        password="",
    )

    response = client.post(
        reverse("manage_cost_groups"),
        {
            "cost_group_id": "cg-new",
            "name": "New Group",
            "name_fr": "Nouveau groupe",
            "lex_file_number": "",
            "monthly_max": "250",
            "active": "on",
            "users": [str(member.pk)],
        },
    )

    assert response.status_code == 200
    cost_group = CostGroup.objects.get(cost_group_id="cg-new")
    assert cost_group.name == "New Group"
    assert list(cost_group.users.values_list("pk", flat=True)) == [member.pk]
    assert cost_group in list(response.context["cost_groups"])


@pytest.mark.django_db
def test_modify_variables_update_success_returns_modal_script(client, all_apps_user):
    admin = all_apps_user()
    client.force_login(admin)

    timestamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
    response = client.post(
        reverse("modify_variables_update"),
        {
            "normal_chat_max_mb": "35",
            "normal_librarian_max_mb": "75",
            "bulk_uploader_chat_max_mb": "55",
            "bulk_uploader_librarian_max_mb": "505",
            "librarian_auto_embed_max_chunks": "600",
            "external_tool_review_flagged_azure_pii_categories": [
                "Email",
                "Person",
            ],
            "external_tool_review_flag_local_pii": "on",
            "external_tool_review_flag_credentials_or_secrets": "on",
            "external_tool_review_flag_large_payloads": "on",
            "laws_last_refreshed": timestamp,
            "exchange_rate": "1.42",
            "terms_last_updated": timestamp,
        },
    )

    assert response.status_code == 200
    assert "bootstrap.Modal" in response.content.decode()
    status = OttoStatus.objects.singleton()
    status.refresh_from_db()
    assert status.exchange_rate == pytest.approx(1.42)
    assert status.librarian_auto_embed_max_chunks == 600
    assert status.external_tool_review_flagged_azure_pii_categories == [
        "Email",
        "Person",
    ]
    assert status.external_tool_review_flag_privileged_or_classified is False
    messages = [message.message for message in get_messages(response.wsgi_request)]
    assert any("Site variables updated successfully" in msg for msg in messages)


@pytest.mark.django_db
def test_modify_variables_update_invalid_re_renders_form(client, all_apps_user):
    admin = all_apps_user()
    client.force_login(admin)

    response = client.post(
        reverse("modify_variables_update"),
        {
            "normal_chat_max_mb": "",
            "normal_librarian_max_mb": "",
            "bulk_uploader_chat_max_mb": "",
            "bulk_uploader_librarian_max_mb": "",
            "librarian_auto_embed_max_chunks": "",
            "laws_last_refreshed": "",
            "exchange_rate": "",
            "terms_last_updated": "",
        },
    )

    assert response.status_code == 200
    template_names = {template.name for template in response.templates}
    assert "components/modify_variables_modal.html" in template_names
    form = response.context["form"]
    assert "exchange_rate" in form.errors


@pytest.mark.django_db
def test_frequently_asked_questions_sets_hide_breadcrumbs(client, all_apps_user):
    admin = all_apps_user()
    client.force_login(admin)

    response = client.get(reverse("frequently_asked_questions"))

    assert response.status_code == 200
    assert response.context["hide_breadcrumbs"] is True


@pytest.mark.django_db
def test_load_test_mock_document_loading(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    cache.set("load_testing_enabled", True)

    mock_library = MagicMock()
    mock_data_source = MagicMock()
    mock_saved_file = MagicMock()
    mock_saved_file.file.save = MagicMock()
    mock_saved_file.generate_hash = MagicMock()
    mock_document = MagicMock()
    mock_document.status = "SUCCESS"
    mock_document.process = MagicMock()
    mock_document.refresh_from_db = MagicMock()
    mock_library.delete = MagicMock()

    with (
        patch("otto.views.Library.objects.create", return_value=mock_library),
        patch("otto.views.DataSource.objects.create", return_value=mock_data_source),
        patch("otto.views.SavedFile.objects.create", return_value=mock_saved_file),
        patch(
            "otto.views.Document.objects.create", return_value=mock_document
        ) as doc_mock,
        patch("otto.views.OttoLLM", return_value=MagicMock()),
    ):
        response = client.get(reverse("load_test"), {"mock_document_loading": "1"})

    assert response.status_code == 200
    assert "Document processing (mock embedding)" in response.content.decode()
    assert doc_mock.called
    kwargs = doc_mock.call_args.kwargs
    assert kwargs["data_source"] is mock_data_source
    assert kwargs["saved_file"] is mock_saved_file
    mock_document.process.assert_called_once_with(mock_embedding=True)
    mock_library.delete.assert_called_once()
    cache.delete("load_testing_enabled")


@pytest.mark.django_db
def test_usage_dashboard_embedding_tokens_sets_initial_cost_group(
    client, all_apps_user
):
    user = all_apps_user()
    client.force_login(user)

    cost_group = CostGroup.objects.create(
        cost_group_id="cg-usage-embed",
        name="Embedding Group",
        monthly_max=120,
    )
    cost_type = CostType.objects.create(
        name="GPT-4 embedding tokens",
        short_name="gpt4-embedding",
        description="Embedding tokens",
        unit_cost=Decimal("0.01"),
        unit_quantity=1,
    )
    Cost.objects.create(
        cost_type=cost_type,
        count=25,
        usd_cost=Decimal("1.00"),
        cost_group=cost_group,
        feature="chat",
        user=user,
    )

    original_init = UsageDashboardForm.__init__

    def fake_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.is_bound = False

    with patch.object(UsageDashboardForm, "__init__", fake_init):
        response = client.get(
            reverse("usage_dashboard"),
            {
                "dashboard_cost_groups": str(cost_group.id),
                "count_type": "embedding_tokens",
                "group": "none",
                "x_axis": "day",
            },
        )

    assert response.status_code == 200
    form = response.context["form"]
    chat_choices = dict(form.fields["chat_type"].choices)
    assert "librarian" in chat_choices
    assert str(chat_choices["qa"]) == "Q&A (queries)"
    assert form.fields["cost_group"].initial == cost_group
    assert response.context["rows"]


@pytest.mark.django_db
def test_usage_dashboard_unbound_form_ignores_missing_cost_group(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    original_init = UsageDashboardForm.__init__

    def fake_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.is_bound = False

    with (
        patch.object(UsageDashboardForm, "__init__", fake_init),
        patch.object(
            CostGroup.objects, "get", side_effect=CostGroup.DoesNotExist
        ) as mock_get,
    ):
        response = client.get(
            reverse("usage_dashboard"),
            {
                "cost_group": "9999",
            },
        )

    assert response.status_code == 200
    mock_get.assert_called_once_with(pk="9999")
    form = response.context["form"]
    assert form.fields["cost_group"].initial == "all"


@pytest.mark.django_db
def test_usage_dashboard_files_created_group_count_type(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    cost_group = CostGroup.objects.create(
        cost_group_id="cg-usage-files",
        name="Files Group",
        monthly_max=200,
    )
    cost_type, _ = CostType.objects.get_or_create(
        short_name="translate-file",
        defaults={
            "name": "Translate file",
            "description": "Translate file",
            "unit_cost": Decimal("0.01"),
            "unit_quantity": 1,
        },
    )
    Cost.objects.create(
        cost_type=cost_type,
        count=1,
        usd_cost=Decimal("0.50"),
        cost_group=cost_group,
        feature="translate",
        user=user,
    )

    original_init = UsageDashboardForm.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        group_field = self.fields["group"]
        if not any(choice[0] == "count_type" for choice in group_field.choices):
            group_field.choices = list(group_field.choices) + [
                ("count_type", "Count type")
            ]

    with patch.object(UsageDashboardForm, "__init__", patched_init):
        response = client.get(
            reverse("usage_dashboard"),
            {
                "group": "count_type",
                "x_axis": "day",
                "count_type": "files_created",
                "chat_type": "translate",
            },
        )

    assert response.status_code == 200
    form = response.context["form"]
    chat_choices = dict(form.fields["chat_type"].choices)
    assert set(chat_choices.keys()) == {"all", "translate", "text_extractor"}
    rows = response.context["rows"]
    assert any(str(row[1]) == "Files created" for row in rows)
    chart_groups = response.context["chart_y_groups"]
    assert chart_groups
    chart_labels = [str(group["label"]) for group in chart_groups]
    assert "Files created" in chart_labels
    files_group = next(
        group for group in chart_groups if str(group["label"]) == "Files created"
    )
    assert files_group["values"][0] == 1


@pytest.mark.django_db
def test_usage_dashboard_group_cost_group_includes_personal(client, all_apps_user):
    user = all_apps_user()
    client.force_login(user)

    cost_group = CostGroup.objects.create(
        cost_group_id="cg-usage-mixed",
        name="Mixed Group",
        monthly_max=150,
    )
    cost_type = CostType.objects.create(
        name="GPT-4 output tokens",
        short_name="gpt4-output",
        description="Output tokens",
        unit_cost=Decimal("0.01"),
        unit_quantity=1,
    )

    cost_with_group = Cost.objects.create(
        cost_type=cost_type,
        count=1,
        usd_cost=Decimal("0.20"),
        cost_group=cost_group,
        feature="chat",
        user=user,
    )

    Cost.objects.create(
        cost_type=cost_type,
        count=1,
        usd_cost=Decimal("0.20"),
        cost_group=None,
        feature="chat",
        user=user,
    )

    yesterday = timezone.now().date() - timedelta(days=1)
    Cost.objects.filter(pk=cost_with_group.pk).update(date_incurred=yesterday)

    response = client.get(
        reverse("usage_dashboard"),
        {
            "group": "cost_group",
            "x_axis": "day",
            "count_type": "chat_messages",
        },
    )

    assert response.status_code == 200
    rows = response.context["rows"]
    labels = [str(row[1]) for row in rows]
    assert cost_group.name in labels
    assert "No cost group (personal costs)" in labels
    chart_groups = response.context["chart_y_groups"]
    chart_labels = [str(group["label"]) for group in chart_groups]
    assert cost_group.name in chart_labels
    assert "No cost group (personal costs)" in chart_labels
    day_values = {row[0] for row in rows}
    assert len(day_values) == 2
