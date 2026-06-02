import json
import re
from io import BytesIO
from unittest.mock import patch
from urllib.parse import quote
from zipfile import ZipFile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import override

import pytest
from chat_next.models import Chat, ChatSettings, Message, Skill, SkillTag

from otto.models import Notification, Team, TeamMembership

from librarian.models import DataSource, Document, Library, SavedFile


@pytest.mark.django_db
def test_chat_sidebar_libraries_button_fetches_modal_content(client, all_apps_user):
    user = all_apps_user()
    chat = Chat.objects.create(user=user, title="Test")
    client.force_login(user)

    response = client.get(reverse("chat_next:chat", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="editLibrariesButton"' in content
    assert f'hx-get="{reverse("chat_next:modal_libraries", args=[chat.id])}"' in content
    assert 'data-bs-target="#chat-next-modal"' in content
    assert 'hx-target="#chat-next-modal-content"' in content
    assert 'id="left-sidebar-scroll"' in content
    assert 'id="chat-search-toggle"' in content
    assert 'id="close-left-sidebar"' in content


@pytest.mark.django_db
def test_settings_modal_shows_context_management_in_advanced_tab(client, all_apps_user):
    user = all_apps_user()
    chat = Chat.objects.create(user=user, title="Test")
    client.force_login(user)

    response = client.get(reverse("chat_next:settings_modal", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Advanced" in content
    assert "Maximum tool iterations" in content
    assert "Context window management" in content
    assert "Compact" in content
    assert "Truncate" in content
    assert "Show error" in content
    assert "System prompt" not in content
    assert "Include images in context" not in content
    assert "Include PDFs in context" not in content
    assert 'id="reasoning-effort-container"' in content
    assert 'id="verbosity-container"' in content
    assert 'id="temperature-container"' in content
    assert "settings-section-nav" in content
    assert "nav nav-tabs" not in content


@pytest.mark.django_db
def test_handle_approval_renders_streaming_reasoning_container(client, all_apps_user):
    user = all_apps_user("approval-streaming-ui")
    chat = Chat.objects.create(user=user, title="Approval chat")
    message = Message.objects.create(
        chat=chat,
        text="",
        is_bot=True,
        details={
            "processing_steps": [
                {
                    "title": "Code interpreter: Generating code...",
                    "details": "```python\nprint('hi')\n```",
                    "status": "in_progress",
                }
            ],
            "pending_local_tool": {"call_id": "call_123"},
        },
    )
    client.force_login(user)

    response = client.get(
        reverse("chat_next:handle_approval", args=[message.id]),
        {"approved": "true"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert f'id="reasoning-container-{message.id}"' in content
    assert f'id="reasoning-data-{message.id}"' in content
    assert 'data-is-reasoning="true"' in content
    assert "Code interpreter: Generating code..." in content
    assert 'sse-connect="/chat_next/message/' in content
    assert (
        "Query contains no Protected, Classified or privileged information" in content
    )
    assert (
        "Sending a request to a database outside the Government of Canada where the request may be viewed."
        in content
    )
    assert (
        "Outgoing data must not be Protected, Classified or privileged information according to Justice’s"
        in content
    )
    assert "Handling and Safeguarding Sensitive Information" in content
    assert "Handling-Safeguarding-sensitive-information2025-en.pdf" in content


@pytest.mark.django_db
def test_handle_approval_renders_localized_action_required_title(client, all_apps_user):
    user = all_apps_user("approval-streaming-ui-fr")
    chat = Chat.objects.create(user=user, title="Conversation d'autorisation")
    message = Message.objects.create(
        chat=chat,
        text="",
        is_bot=True,
        details={
            "processing_steps": [
                {
                    "title": "Autorisation requise",
                    "details": "{}",
                    "status": "waiting_approval",
                    "is_approval_request": True,
                    "approval_request_id": "call_456",
                }
            ],
            "pending_local_tool": {"call_id": "call_456"},
        },
    )
    client.force_login(user)

    with override("fr"):
        response = client.get(
            reverse("chat_next:handle_approval", args=[message.id]),
            {"approved": "true"},
            HTTP_ACCEPT_LANGUAGE="fr",
        )

    assert response.status_code == 200
    content = response.content.decode()
    assert (
        'data-action-required-title="⚠️ Action requise"' in content
        or 'data-action-required-title="⚠️ Action Required"' in content
    )


@pytest.mark.django_db
def test_chat_page_includes_external_approval_warning_copy_for_saved_pending_message(
    client, all_apps_user
):
    user = all_apps_user("saved-external-approval-ui")
    chat = Chat.objects.create(user=user, title="Pending external approval chat")
    Message.objects.create(
        chat=chat,
        text="",
        is_bot=True,
        details={
            "processing_steps": [
                {
                    "title": "Approval required: Look up TERMIUM Plus",
                    "details": '```json\n{"query": "cabinet confidence", "index": "ent"}\n```',
                    "status": "waiting_approval",
                    "is_approval_request": True,
                    "approval_request_id": "call_termium_pending",
                    "approval_requires_external_warning": True,
                }
            ]
        },
    )
    client.force_login(user)

    response = client.get(reverse("chat_next:chat", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert (
        "Query contains no Protected, Classified or privileged information" in content
    )
    assert (
        "Sending a request to a database outside the Government of Canada where the request may be viewed."
        in content
    )
    assert (
        "Outgoing data must not be Protected, Classified or privileged information according to Justice’s"
        in content
    )
    assert "Handling and Safeguarding Sensitive Information" in content


@pytest.mark.django_db
def test_completed_french_bot_message_shows_translate_processing_steps_button(
    client, all_apps_user
):
    user = all_apps_user("translate-processing-steps-ui")
    chat = Chat.objects.create(user=user, title="French chat")
    message = Message.objects.create(
        chat=chat,
        text="Final answer",
        is_bot=True,
        details={
            "processing_steps": [
                {"title": "Analyze request", "details": "Need more info"},
                {
                    "title": "Used search_library",
                    "details": "```json\n{}\n```",
                    "status": "complete",
                },
            ]
        },
    )
    client.force_login(user)

    response = client.get(
        reverse("chat_next:chat", args=[chat.id]),
        HTTP_ACCEPT_LANGUAGE="fr",
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert 'data-can-translate-reasoning-steps="true"' in content
    assert (
        f'data-translate-processing-steps-url="{reverse("chat_next:translate_processing_steps", args=[message.id])}"'
        in content
    )


@pytest.mark.django_db
def test_completed_english_bot_message_hides_translate_processing_steps_button(
    client, all_apps_user
):
    user = all_apps_user("translate-processing-steps-en")
    chat = Chat.objects.create(user=user, title="English chat")
    message = Message.objects.create(
        chat=chat,
        text="Final answer",
        is_bot=True,
        details={
            "processing_steps": [
                {"title": "Analyze request", "details": "Need more info"}
            ]
        },
    )
    client.force_login(user)

    response = client.get(
        reverse("chat_next:chat", args=[chat.id]),
        HTTP_ACCEPT_LANGUAGE="en",
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert (
        reverse("chat_next:translate_processing_steps", args=[message.id])
        not in content
    )
    assert 'data-can-translate-reasoning-steps="false"' in content


@pytest.mark.django_db
def test_translate_processing_steps_endpoint_persists_french_reasoning_only(
    client, all_apps_user
):
    user = all_apps_user("translate-processing-steps-endpoint")
    chat = Chat.objects.create(user=user, title="Translate chat")
    message = Message.objects.create(
        chat=chat,
        text="Final answer",
        is_bot=True,
        details={
            "processing_steps": [
                {"title": "Analyze request", "details": "Need more info"},
                {
                    "title": "Used: Search library",
                    "details": '```json\n{\n  "query": "test"\n}\n```',
                    "status": "complete",
                },
            ]
        },
    )
    client.force_login(user)

    with patch(
        "chat_next.views.translate_reasoning_processing_steps",
        return_value=[
            {"title": "Analyser la demande", "details": "Besoin de plus d'information"},
            {
                "title": "Used: Search library",
                "details": '```json\n{\n  "query": "test"\n}\n```',
                "status": "complete",
            },
        ],
    ):
        response = client.post(
            reverse("chat_next:translate_processing_steps", args=[message.id]),
            HTTP_ACCEPT_LANGUAGE="fr",
        )

    assert response.status_code == 200
    message.refresh_from_db()
    fr_translation = message.details["processing_steps_translations"]["fr"]
    assert fr_translation["status"] == "complete"
    assert fr_translation["steps"][0]["title"] == "Analyser la demande"
    assert fr_translation["steps"][1]["title"] == "Used: Search library"
    assert f'id="reasoning-section-{message.id}"' in response.content.decode()


@pytest.mark.django_db
def test_get_message_html_uses_french_processing_step_translation(
    client, all_apps_user
):
    user = all_apps_user("translate-processing-steps-html")
    chat = Chat.objects.create(user=user, title="Recovery chat")
    message = Message.objects.create(
        chat=chat,
        text="Final answer",
        is_bot=True,
        details={
            "processing_steps": [
                {"title": "Analyze request", "details": "Need more info"}
            ],
            "processing_steps_translations": {
                "fr": {
                    "status": "complete",
                    "steps": [
                        {
                            "title": "Analyser la demande",
                            "details": "Besoin de plus d'information",
                        }
                    ],
                }
            },
        },
    )
    client.force_login(user)

    response = client.get(
        reverse("chat_next:get_message_html", args=[message.id]),
        HTTP_ACCEPT_LANGUAGE="fr",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["complete"] is True
    assert "Analyser la demande" in payload["html"]
    assert 'hx-swap-oob="true"' in payload["html"]


@pytest.mark.django_db
def test_streaming_response_templates_include_chat_next_message_html_url(all_apps_user):
    user = all_apps_user("streaming-response-recovery-url")
    chat = Chat.objects.create(user=user, title="Recovery chat")
    message = Message.objects.create(chat=chat, text="", is_bot=True)

    expected_url = reverse("chat_next:get_message_html", args=[message.id])

    streaming_html = render_to_string(
        "chat_next/components/streaming_response.html",
        {"message": message, "cost_approved": False},
    )
    approval_html = render_to_string(
        "chat_next/components/approval_streaming.html",
        {
            "message": message,
            "approved": "true",
            "cost_cancelled": "false",
            "processing_steps_json": "[]",
        },
    )

    assert f'data-message-html-url="{expected_url}"' in streaming_html
    assert f'data-message-html-url="{expected_url}"' in approval_html


@pytest.mark.django_db
def test_chat_message_shows_compaction_indicator_for_explicit_compaction_step(
    all_apps_user,
):
    user = all_apps_user("message-compaction-indicator-explicit")
    chat = Chat.objects.create(user=user, title="Compaction chat")
    message = Message.objects.create(
        chat=chat,
        text="Compacted answer",
        is_bot=True,
        details={
            "usage": {"input_tokens": 400, "output_tokens": 100},
            "raw_processing_steps": [
                {
                    "type": "tool_call",
                    "tool_type": "compaction",
                    "status": "completed",
                    "details": {"tool_label": "Compacted conversation"},
                }
            ],
        },
    )

    rendered = render_to_string(
        "chat_next/components/chat_message.html",
        {"message": message, "swap_oob": False},
    )

    assert "message-compaction-indicator" in rendered
    assert "Conversation was compacted during this response." in rendered


@pytest.mark.django_db
def test_chat_message_shows_compaction_indicator_when_context_usage_drops(
    all_apps_user,
):
    user = all_apps_user("message-compaction-indicator-inferred")
    chat = Chat.objects.create(user=user, title="Compaction heuristic chat")
    Message.objects.create(
        chat=chat,
        text="Before compaction",
        is_bot=True,
        details={"usage": {"input_tokens": 180000, "output_tokens": 12000}},
    )
    compacted_message = Message.objects.create(
        chat=chat,
        text="After compaction",
        is_bot=True,
        details={"usage": {"input_tokens": 130000, "output_tokens": 4000}},
    )

    rendered = render_to_string(
        "chat_next/components/chat_message.html",
        {"message": compacted_message, "swap_oob": False},
    )

    assert "message-compaction-indicator" in rendered
    assert "Conversation was compacted during this response." in rendered


@pytest.mark.django_db
def test_chat_message_hides_compaction_indicator_for_small_usage_drop(
    all_apps_user,
):
    user = all_apps_user("message-compaction-indicator-small-drop")
    chat = Chat.objects.create(user=user, title="Compaction small drop chat")
    Message.objects.create(
        chat=chat,
        text="Before small drop",
        is_bot=True,
        details={"usage": {"input_tokens": 180000, "output_tokens": 12000}},
    )
    later_message = Message.objects.create(
        chat=chat,
        text="After small drop",
        is_bot=True,
        details={"usage": {"input_tokens": 172000, "output_tokens": 9000}},
    )

    rendered = render_to_string(
        "chat_next/components/chat_message.html",
        {"message": later_message, "swap_oob": False},
    )

    assert "message-compaction-indicator" not in rendered


@pytest.mark.django_db
def test_chat_message_hides_compaction_indicator_for_reasoning_only_drop(
    all_apps_user,
):
    user = all_apps_user("message-compaction-indicator-reasoning-drop")
    chat = Chat.objects.create(user=user, title="Compaction reasoning-only chat")
    Message.objects.create(
        chat=chat,
        text="Before reasoning-heavy response",
        is_bot=True,
        details={
            "usage": {
                "input_tokens": 150000,
                "output_tokens": 30000,
                "reasoning_tokens": 25000,
            }
        },
    )
    later_message = Message.objects.create(
        chat=chat,
        text="After reasoning-light response",
        is_bot=True,
        details={
            "usage": {
                "input_tokens": 150000,
                "output_tokens": 5000,
                "reasoning_tokens": 0,
            }
        },
    )

    rendered = render_to_string(
        "chat_next/components/chat_message.html",
        {"message": later_message, "swap_oob": False},
    )

    assert "message-compaction-indicator" not in rendered


@pytest.mark.django_db
def test_settings_modal_explains_maximum_tool_iterations(client, all_apps_user):
    user = all_apps_user("settings-modal-max-iterations-help")
    chat = Chat.objects.create(user=user, title="Settings helper text chat")
    client.force_login(user)

    response = client.get(reverse("chat_next:settings_modal", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert (
        "Otto will pause for approval after this many chained tool-call rounds."
        in content
    )


@pytest.mark.django_db
def test_settings_modal_hx_post_autosaves_without_rerendering_modal(
    client, all_apps_user
):
    user = all_apps_user()
    chat = Chat.objects.create(user=user, title="Test")
    settings, _ = ChatSettings.objects.get_or_create_for_user(user)
    client.force_login(user)

    response = client.post(
        reverse("chat_next:settings_modal", args=[chat.id]),
        {
            "user_display_name": settings.user_display_name,
            "job_description": settings.job_description,
            "global_instructions": settings.global_instructions,
            "chat_model": settings.chat_model,
            "chat_temperature": settings.chat_temperature,
            "chat_reasoning_effort": settings.chat_reasoning_effort,
            "chat_verbosity": settings.chat_verbosity,
            "chat_enabled_tools": settings.chat_enabled_tools,
            "chat_max_iterations": 7,
            "chat_context_management": settings.chat_context_management,
            "active_tab": "settings-tab-advanced",
            **({"send_name_to_model": "on"} if settings.send_name_to_model else {}),
        },
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert response.headers["HX-Reswap"] == "none"
    assert response.headers["HX-Trigger"] == "settings-saved"
    assert response.content.decode() == ""

    settings = ChatSettings.objects.get(user=user)
    assert settings.chat_max_iterations == 7


@pytest.mark.django_db
def test_chat_page_renders_compact_model_selector(client, all_apps_user):
    user = all_apps_user("compact-model-selector")
    chat = Chat.objects.create(user=user, title="Selector chat")
    client.force_login(user)

    response = client.get(reverse("chat_next:chat", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="chat-model-selector"' in content
    assert 'data-bs-auto-close="false"' in content
    assert 'name="chat-model-selector-chat_model"' in content
    assert 'name="chat-model-selector-chat_reasoning_effort"' in content
    assert 'name="chat-model-selector-chat_verbosity"' in content


@pytest.mark.django_db
def test_chat_page_renders_copy_dropdown_options_for_messages(client, all_apps_user):
    user = all_apps_user("copy-dropdown-ui")
    chat = Chat.objects.create(user=user, title="Copy dropdown chat")
    Message.objects.create(
        chat=chat,
        text="## Heading\n\nThis is **markdown**.",
        is_bot=True,
    )
    client.force_login(user)

    response = client.get(reverse("chat_next:chat", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "copy-message-dropdown" in content
    assert "copy-message-menu-toggle" in content
    assert "Copy as markdown" in content
    assert "Copy as rich text" in content
    assert "copy_as_markdown" in content
    assert "copy_as_rich_text" in content


@pytest.mark.django_db
def test_chat_page_search_query_renders_sidebar_matches_without_error(
    client, all_apps_user
):
    user = all_apps_user("chat-search-sidebar-match")
    chat = Chat.objects.create(user=user, title="Searchable chat")
    matching_message = Message.objects.create(
        chat=chat,
        text="This message mentions a2aj so the search result can deep-link here.",
        is_bot=False,
    )
    client.force_login(user)

    response = client.get(reverse("chat_next:chat", args=[chat.id]), {"search": "a2aj"})

    assert response.status_code == 200
    content = response.content.decode()
    assert f"#message_{matching_message.id}" in content
    assert "a2aj" in content.lower()


@pytest.mark.django_db
def test_chat_page_renders_updated_gpt5_groups_and_effort_metadata(
    client, all_apps_user
):
    user = all_apps_user("gpt5-groups")
    chat = Chat.objects.create(user=user, title="GPT-5 groups")
    client.force_login(user)

    response = client.get(reverse("chat_next:chat", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Recommended" in content
    assert "Other" in content
    assert 'data-supported-reasoning-efforts="none,low,medium,high,xhigh"' in content
    assert 'data-supported-reasoning-efforts="minimal,low,medium,high"' in content


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("selected_model", "expected_bot_name"),
    [
        ("gpt-5.2", "GPT-5.2"),
        ("gpt-5.4", "GPT-5.4"),
        ("gpt-5.4-mini", "GPT-5.4-mini"),
        ("gpt-5.4-nano", "GPT-5.4-nano"),
    ],
)
def test_compact_model_selector_post_updates_model_settings(
    client, all_apps_user, selected_model, expected_bot_name
):
    user = all_apps_user("compact-model-save")
    chat = Chat.objects.create(user=user, title="Selector save")
    settings, _ = ChatSettings.objects.get_or_create_for_user(user)
    initial_chat_model = settings.chat_model
    client.force_login(user)

    response = client.post(
        reverse("chat_next:chat_message", args=[chat.id]),
        {
            "user-message": "Hello there",
            "chat-model-selector-chat_model": selected_model,
            "chat-model-selector-chat_reasoning_effort": "high",
            "chat-model-selector-chat_verbosity": "high",
        },
    )

    assert response.status_code == 200

    settings.refresh_from_db()
    assert settings.chat_model == initial_chat_model

    bot_message = chat.messages.filter(is_bot=True).latest("id")
    assert bot_message.bot_name == expected_bot_name
    assert bot_message.details["model_overrides"] == {
        "chat_model": selected_model,
        "chat_reasoning_effort": "high",
        "chat_verbosity": "high",
    }


@pytest.mark.django_db
def test_rerun_prompt_uses_current_model_selector_values(client, all_apps_user):
    user = all_apps_user("rerun-current-model-selector")
    chat = Chat.objects.create(user=user, title="Rerun selector chat")
    settings, _ = ChatSettings.objects.get_or_create_for_user(user)
    settings.chat_model = "gpt-5.4-mini"
    settings.chat_reasoning_effort = "medium"
    settings.chat_verbosity = "medium"
    settings.save(
        update_fields=[
            "chat_model",
            "chat_reasoning_effort",
            "chat_verbosity",
        ]
    )

    original = Message.objects.create(chat=chat, text="Hello", is_bot=False)
    stale_bot = Message.objects.create(
        chat=chat,
        text="Old response",
        is_bot=True,
        parent=original,
    )
    stale_user = Message.objects.create(chat=chat, text="Follow up", is_bot=False)

    client.force_login(user)

    response = client.post(
        reverse("chat_next:rerun_prompt", args=[original.id]),
        {
            "chat-model-selector-chat_model": "gpt-5.2",
            "chat-model-selector-chat_reasoning_effort": "high",
            "chat-model-selector-chat_verbosity": "high",
        },
    )

    assert response.status_code == 200
    assert not Message.objects.filter(id=stale_bot.id).exists()
    assert not Message.objects.filter(id=stale_user.id).exists()

    new_bot = Message.objects.filter(chat=chat, is_bot=True).get()
    assert new_bot.parent_id == original.id
    assert new_bot.text == ""
    assert new_bot.bot_name == "GPT-5.2"
    assert new_bot.details["model_overrides"] == {
        "chat_model": "gpt-5.2",
        "chat_reasoning_effort": "high",
        "chat_verbosity": "high",
    }

    settings.refresh_from_db()
    assert settings.chat_model == "gpt-5.4-mini"
    assert settings.chat_reasoning_effort == "medium"
    assert settings.chat_verbosity == "medium"

    html = response.content.decode()
    assert "awaiting-response" in html
    assert f"id='message_{stale_bot.id}' hx-swap-oob='delete'" in html


@pytest.mark.django_db
def test_settings_modal_labels_model_settings_as_defaults(client, all_apps_user):
    user = all_apps_user("default-model-labels")
    chat = Chat.objects.create(user=user, title="Default model labels")
    client.force_login(user)

    response = client.get(reverse("chat_next:settings_modal", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Default model" in content
    assert "AI models" not in content


@pytest.mark.django_db
def test_chat_next_model_selectors_do_not_show_gpt4_series(client, all_apps_user):
    user = all_apps_user("no-gpt4-selector")
    chat = Chat.objects.create(user=user, title="No GPT4 chat")
    client.force_login(user)

    response = client.get(reverse("chat_next:chat", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "GPT-4.1" not in content
    assert "GPT-4.1-mini" not in content
    assert "GPT-4.1-nano" not in content


@pytest.mark.django_db
def test_existing_gpt4_chat_setting_is_normalized_to_gpt5(client, all_apps_user):
    user = all_apps_user("normalize-gpt4")
    chat = Chat.objects.create(user=user, title="Normalize GPT4")
    settings, _ = ChatSettings.objects.get_or_create_for_user(user)
    settings.chat_model = "gpt-4.1-mini"
    settings.save(update_fields=["chat_model"])
    client.force_login(user)

    response = client.get(reverse("chat_next:settings_modal", args=[chat.id]))

    assert response.status_code == 200
    settings.refresh_from_db()
    assert settings.chat_model == "gpt-5.4-mini"


@pytest.mark.django_db
def test_read_only_skill_view_shows_inner_toggle_and_new_skill_button(
    client, all_apps_user, basic_user
):
    owner = all_apps_user("owner")
    viewer = basic_user("viewer", accept_terms=True)
    chat = Chat.objects.create(user=viewer, title="Viewer chat")
    skill = Skill.objects.create(
        display_name="Shared skill",
        description="A shared skill",
        body="Do the thing.",
        owner=owner,
        sharing_option="others",
    )
    skill.accessible_to.add(viewer)
    client.force_login(viewer)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "New skill" in content
    assert 'id="current-skill-enabled-toggle"' in content
    assert reverse("chat_next:toggle_skill", args=[chat.id, skill.id]) in content
    assert "Save changes" not in content
    assert "Share with others" not in content
    assert 'name="skill_tags"' in content
    assert 'name="skill_tags_fr"' not in content
    assert "Tags (FR)" not in content


@pytest.mark.django_db
def test_team_shared_skill_view_is_available_to_team_member(client, all_apps_user):
    owner = all_apps_user("team-shared-skill-owner")
    member = all_apps_user("team-shared-skill-member")
    chat = Chat.objects.create(user=member, title="Team viewer chat")
    team = Team.objects.create(name="Shared skill team", created_by=owner)
    TeamMembership.objects.create(team=team, user=owner, role="admin")
    TeamMembership.objects.create(team=team, user=member, role="member")

    skill = Skill.objects.create(
        display_name="Team View Skill",
        description="Visible through team sharing",
        body="Prompt",
        owner=owner,
        sharing_option="others",
    )
    skill.accessible_to_teams.add(team)

    client.force_login(member)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    assert "Team View Skill" in response.content.decode()


@pytest.mark.django_db
def test_edit_skill_returns_404_for_inaccessible_private_skill(client, all_apps_user):
    owner = all_apps_user("private-skill-owner")
    viewer = all_apps_user("private-skill-viewer")
    chat = Chat.objects.create(user=viewer, title="Private skill chat")
    skill = Skill.objects.create(
        display_name="Private Hidden Skill",
        description="Should stay private",
        body="Prompt",
        owner=owner,
        sharing_option="private",
    )

    client.force_login(viewer)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 404


@pytest.mark.django_db
def test_toggle_skill_can_return_to_read_only_skill_view(
    client, all_apps_user, basic_user
):
    owner = all_apps_user("owner-toggle")
    viewer = basic_user("viewer-toggle", accept_terms=True)
    chat = Chat.objects.create(user=viewer, title="Viewer chat")
    skill = Skill.objects.create(
        display_name="Toggle skill",
        description="Toggle me",
        body="Do the thing.",
        owner=owner,
        sharing_option="others",
    )
    skill.accessible_to.add(viewer)
    client.force_login(viewer)

    response = client.post(
        reverse("chat_next:toggle_skill", args=[chat.id, skill.id]),
        {"return_to_skill": "true"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="current-skill-enabled-toggle"' in content
    assert "checked" in content
    assert "Toggle skill" in content
    assert 'id="active-skills-panel"' in content


@pytest.mark.django_db
def test_otto_admin_can_edit_public_featured_skill_with_warning(client, all_apps_user):
    owner = all_apps_user("skill-owner")
    admin_user = all_apps_user("admin-editor")
    chat = Chat.objects.create(user=admin_user, title="Admin chat")
    skill = Skill.objects.create(
        display_name="Default skill",
        description="A default skill",
        body="Default body.",
        owner=owner,
        sharing_option="everyone",
        is_featured=True,
    )
    client.force_login(admin_user)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Changes will be visible to all Otto users" in content
    assert "New tags you create may be visible to other users" in content
    assert "Save changes" in content
    assert "disabled" not in content.split("<fieldset", 1)[1].split(">", 1)[0]


@pytest.mark.django_db
def test_otto_admin_can_change_sharing_for_fixture_style_default_skill(
    client, all_apps_user
):
    admin_user = all_apps_user("admin-default-skill-editor")
    chat = Chat.objects.create(user=admin_user, title="Admin default skill chat")
    skill = Skill.objects.create(
        display_name="Fixture default skill",
        description="System default skill",
        body="Default body.",
        owner=None,
        sharing_option="everyone",
        is_system=True,
    )
    client.force_login(admin_user)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Make private" in content
    assert "Share with specific people" in content
    assert "Share with everyone" in content
    assert "Only the skill owner can make it private or public." not in content

    post_response = client.post(
        reverse("chat_next:edit_skill", args=[chat.id, skill.id]),
        {
            "display_name_en": "Fixture default skill",
            "display_name_fr": "",
            "description_en": "System default skill",
            "description_fr": "",
            "body_en": "Default body.",
            "body_fr": "",
            "sharing_option": "others",
            "context_hints": "[]",
            "skill_tags": "[]",
        },
    )

    assert post_response.status_code == 200
    skill.refresh_from_db()
    assert skill.sharing_option == "others"


@pytest.mark.django_db
def test_skills_browser_defaults_to_no_filter_checkboxes_selected(
    client, all_apps_user
):
    user = all_apps_user("skills-defaults")
    chat = Chat.objects.create(user=user, title="Skills chat")
    Skill.objects.create(
        display_name="Defaults skill",
        description="Skill for defaults test",
        body="Test body",
        owner=user,
    )
    client.force_login(user)

    response = client.get(reverse("chat_next:get_skills", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="filter-mine"' in content
    assert 'id="filter-featured"' in content
    assert 'id="filter-everyone"' in content
    assert 'id="filter-shared-with-me"' in content
    assert 'id="filter-enabled-only"' in content
    assert "skill-filter-enabled-hidden" in content

    # Ensure none of the checkbox filter inputs are checked by default
    assert 'id="filter-mine"\n               checked' not in content
    assert 'id="filter-featured"\n               checked' not in content
    assert 'id="filter-everyone"\n               checked' not in content
    assert 'id="filter-shared-with-me"\n               checked' not in content
    assert 'id="filter-enabled-only"\n               checked' not in content
    assert "setHiddenValue('skill-filter-enabled-hidden', '');" in content


@pytest.mark.django_db
def test_skills_browser_includes_skill_creator_shortcut_submit_handler(
    client, all_apps_user
):
    user = all_apps_user("skills-creator-shortcut")
    chat = Chat.objects.create(user=user, title="Skill creator shortcut chat")
    client.force_login(user)

    response = client.get(reverse("chat_next:get_skills", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    if "submitSkillCreatorPrompt(" in content:
        assert "window.submitSkillCreatorPrompt = function" in content


@pytest.mark.django_db
def test_skills_browser_enabled_only_filter_shows_enabled_skills(client, all_apps_user):
    user = all_apps_user("skills-enabled-only")
    chat = Chat.objects.create(user=user, title="Enabled filter chat")

    enabled_skill = Skill.objects.create(
        display_name="Enabled skill",
        description="Already enabled",
        body="Test body",
        owner=user,
    )
    Skill.objects.create(
        display_name="Disabled skill",
        description="Not enabled",
        body="Test body",
        owner=user,
    )
    settings, _ = ChatSettings.objects.get_or_create_for_user(user)
    settings.enabled_skills.add(enabled_skill)
    client.force_login(user)

    response = client.get(
        reverse("chat_next:get_skills", args=[chat.id]),
        {"enabled": "1"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Enabled skill" in content
    assert "Disabled skill" not in content
    assert 'id="filter-enabled-only"' in content
    assert 'id="filter-enabled-only"\n               checked' in content


@pytest.mark.django_db
def test_skills_browser_search_defaults_sort_to_most_relevant(client, all_apps_user):
    user = all_apps_user("skills-search-sort")
    chat = Chat.objects.create(user=user, title="Search sort chat")
    Skill.objects.create(
        display_name="Searchable skill",
        description="Matches query",
        body="Test body",
        owner=user,
    )
    client.force_login(user)

    response = client.get(
        reverse("chat_next:get_skills", args=[chat.id]),
        {"q": "Searchable"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    relevant_option = content.split('<option value="relevant"', 1)[1].split(
        "</option>", 1
    )[0]
    assert "selected" in relevant_option


@pytest.mark.django_db
def test_skills_browser_preserves_trailing_space_in_search_input(client, all_apps_user):
    user = all_apps_user("skills-search-space")
    chat = Chat.objects.create(user=user, title="Search spacing chat")
    Skill.objects.create(
        display_name="Space Preserved Skill",
        description="Matches spaced query",
        body="Test body",
        owner=user,
    )
    client.force_login(user)

    response = client.get(
        reverse("chat_next:get_skills", args=[chat.id]),
        {"q": "Space "},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="skill-filter-search"' in content
    assert 'value="Space "' in content


@pytest.mark.django_db
def test_skills_browser_shows_filtered_over_total_count(client, all_apps_user):
    user = all_apps_user("skills-counts")
    other = all_apps_user("skills-counts-other")
    chat = Chat.objects.create(user=user, title="Counts chat")

    Skill.objects.create(
        display_name="Mine count",
        description="Owned by current user",
        body="Test body",
        owner=user,
    )
    Skill.objects.create(
        display_name="Public count",
        description="Public skill",
        body="Test body",
        owner=other,
        sharing_option="everyone",
    )

    client.force_login(user)

    response = client.get(
        reverse("chat_next:get_skills", args=[chat.id]),
        {"sharing": "mine"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    total_visible = Skill.objects.get_accessible(user).count()
    assert f"Displaying 1/{total_visible} skills" in content


@pytest.mark.django_db
def test_skills_browser_popular_sort_uses_load_count_and_add_does_not_increment(
    client, all_apps_user
):
    user = all_apps_user("skills-popular")
    chat = Chat.objects.create(user=user, title="Popular chat")

    Skill.objects.create(
        display_name="Popular skill",
        description="Popular",
        body="Test body",
        owner=user,
        load_count=5,
    )
    less_popular = Skill.objects.create(
        display_name="Less popular skill",
        description="Less popular",
        body="Test body",
        owner=user,
        load_count=1,
    )

    client.force_login(user)

    response = client.get(reverse("chat_next:get_skills", args=[chat.id]))
    assert response.status_code == 200
    content = response.content.decode()
    assert content.index("Popular skill") < content.index("Less popular skill")

    before = Skill.objects.get(id=less_popular.id).load_count
    add_response = client.post(
        reverse("chat_next:add_skill", args=[chat.id, less_popular.id])
    )
    assert add_response.status_code == 200
    less_popular.refresh_from_db()
    assert less_popular.load_count == before


@pytest.mark.django_db
def test_skills_browser_shared_with_me_includes_team_shared_skill(
    client, all_apps_user
):
    owner = all_apps_user("skills-team-filter-owner")
    member = all_apps_user("skills-team-filter-member")
    chat = Chat.objects.create(user=member, title="Team shared filter chat")
    team = Team.objects.create(name="Skills filter team", created_by=owner)
    TeamMembership.objects.create(team=team, user=owner, role="admin")
    TeamMembership.objects.create(team=team, user=member, role="member")

    team_skill = Skill.objects.create(
        display_name="Team Shared Filter Skill",
        description="Shared with a team",
        body="Prompt",
        owner=owner,
        sharing_option="others",
    )
    team_skill.accessible_to_teams.add(team)

    client.force_login(member)

    response = client.get(
        reverse("chat_next:get_skills", args=[chat.id]),
        {"sharing": "shared_with_me"},
    )

    assert response.status_code == 200
    assert "Team Shared Filter Skill" in response.content.decode()


@pytest.mark.django_db
def test_admin_can_delete_public_skill_and_sees_serious_warning(client, all_apps_user):
    owner = all_apps_user("public-delete-owner")
    admin_user = all_apps_user("public-delete-admin")
    chat = Chat.objects.create(user=admin_user, title="Admin delete chat")

    skill = Skill.objects.create(
        display_name="Public deletable",
        description="Public skill",
        body="Test body",
        owner=owner,
        sharing_option="everyone",
    )

    client.force_login(admin_user)

    # Admin can see delete button + serious warning on public skills
    edit_response = client.get(
        reverse("chat_next:edit_skill", args=[chat.id, skill.id])
    )
    assert edit_response.status_code == 200
    edit_content = edit_response.content.decode()
    assert "SERIOUS ACTION" in edit_content
    assert "Delete" in edit_content

    # Admin can delete it
    delete_response = client.post(
        reverse("chat_next:delete_skill", args=[chat.id, skill.id])
    )
    assert delete_response.status_code == 200
    assert not Skill.objects.filter(id=skill.id).exists()


@pytest.mark.django_db
def test_delete_skill_removes_skill_folder_and_documents(client, all_apps_user):
    user = all_apps_user("skill-delete-folder")
    chat = Chat.objects.create(user=user, title="Delete folder chat")
    skill = Skill.objects.create(
        display_name="Delete folder skill",
        description="Delete me",
        body="Prompt",
        owner=user,
    )
    settings, _ = ChatSettings.objects.get_or_create_for_user(user)
    settings.enabled_skills.add(skill)
    library = user.skill_library or user.create_skill_library()
    data_source = DataSource.objects.create(
        library=library,
        name="Delete me folder",
        skill=skill,
    )
    saved_file = SavedFile.objects.create(openai_file_id="file-delete-skill-folder")
    document = Document.objects.create(
        data_source=data_source,
        saved_file=saved_file,
        filename="delete-me.txt",
        extracted_text="bye",
        status="SUCCESS",
        provenance=Document.PROVENANCE_USER_UPLOAD,
    )
    client.force_login(user)

    response = client.post(reverse("chat_next:delete_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    assert not Skill.objects.filter(id=skill.id).exists()
    assert not settings.enabled_skills.filter(id=skill.id).exists()
    assert not DataSource.objects.filter(id=data_source.id).exists()
    assert not Document.objects.filter(id=document.id).exists()


@pytest.mark.django_db
def test_non_admin_can_see_make_copy_action_without_featured_toggle(
    client, all_apps_user, basic_user
):
    owner = all_apps_user("copy-action-owner")
    viewer = basic_user("copy-action-viewer", accept_terms=True)
    chat = Chat.objects.create(user=viewer, title="Copy action chat")
    skill = Skill.objects.create(
        display_name="Copy action source",
        description="Shared skill",
        body="Prompt",
        owner=owner,
        sharing_option="everyone",
    )

    client.force_login(viewer)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Make a copy" in content
    assert "current-skill-featured-toggle" not in content
    assert "bi-three-dots-vertical" not in content


@pytest.mark.django_db
def test_copy_skill_creates_private_disabled_copy_without_sharing(
    client, all_apps_user
):
    owner = all_apps_user("copy-source-owner")
    copier = all_apps_user("copy-skill-user")
    chat = Chat.objects.create(user=copier, title="Copy skill chat")
    source_skill = Skill.objects.create(
        display_name_en="Copyable skill",
        description_en="Helpful shared skill",
        short_description_en="Short summary",
        body_en="Original prompt",
        owner=owner,
        sharing_option="others",
        is_system=True,
        is_featured=True,
        load_count=7,
        required_tools=["local_terminology"],
        context_hints=[
            {"type": "tool", "id": "local_terminology", "name": "GC Terminology"}
        ],
        tags=["legacy-tag"],
    )
    source_skill.accessible_to.add(copier)
    source_skill.editable_by.add(owner)
    tag = SkillTag.objects.create(name="briefing", name_en="briefing")
    source_skill.skill_tags.add(tag)

    user_settings, _ = ChatSettings.objects.get_or_create_for_user(copier)
    user_settings.enabled_skills.add(source_skill)

    client.force_login(copier)

    response = client.post(
        reverse("chat_next:copy_skill", args=[chat.id, source_skill.id]),
        {"modal_back_url": reverse("chat_next:get_skills", args=[chat.id])},
    )

    assert response.status_code == 200
    copied_skill = Skill.objects.filter(owner=copier).exclude(id=source_skill.id).get()

    assert copied_skill.display_name_en == "Copyable skill (copy)"
    assert copied_skill.description_en == source_skill.description_en
    assert copied_skill.body_en == source_skill.body_en
    assert copied_skill.short_description_en == source_skill.short_description_en
    assert copied_skill.required_tools == source_skill.required_tools
    assert copied_skill.context_hints == source_skill.context_hints
    assert copied_skill.tags == source_skill.tags
    assert copied_skill.sharing_option == "private"
    assert copied_skill.owner == copier
    assert copied_skill.is_featured is False
    assert copied_skill.is_system is False
    assert copied_skill.load_count == 0
    assert copied_skill.accessible_to.count() == 0
    assert copied_skill.editable_by.count() == 0
    assert copied_skill.accessible_to_teams.count() == 0
    assert copied_skill.editable_by_teams.count() == 0
    assert copied_skill.skill_tags.filter(id=tag.id).exists()
    assert not user_settings.enabled_skills.filter(id=copied_skill.id).exists()
    assert "Copyable skill (copy)" in response.content.decode()


@pytest.mark.django_db
def test_copy_skill_rewrites_skill_file_folder_hints_into_new_skill_folder(
    client, all_apps_user
):
    owner = all_apps_user("copy-files-owner")
    copier = all_apps_user("copy-files-user")
    chat = Chat.objects.create(user=copier, title="Copy files chat")
    source_skill = Skill.objects.create(
        display_name_en="Source skill",
        description_en="Uses private skill files",
        body_en="Prompt",
        owner=owner,
        sharing_option="everyone",
    )

    source_library = owner.skill_library or owner.create_skill_library()
    source_data_source = DataSource.objects.create(
        library=source_library,
        name="Source files",
        skill=source_skill,
    )
    saved_file_one = SavedFile.objects.create(openai_file_id="file-copy-skill-one")
    saved_file_two = SavedFile.objects.create(openai_file_id="file-copy-skill-two")
    source_doc_one = Document.objects.create(
        data_source=source_data_source,
        saved_file=saved_file_one,
        filename="one.txt",
        extracted_text="Document one",
        status="SUCCESS",
        provenance=Document.PROVENANCE_USER_UPLOAD,
    )
    source_doc_two = Document.objects.create(
        data_source=source_data_source,
        saved_file=saved_file_two,
        filename="two.txt",
        extracted_text="Document two",
        status="SUCCESS",
        provenance=Document.PROVENANCE_USER_UPLOAD,
    )
    source_skill.context_hints = [
        {
            "type": "folder",
            "id": str(source_data_source.id),
            "name": source_data_source.name,
            "parent_library_id": str(source_library.id),
        },
        {
            "type": "document",
            "id": source_doc_one.id,
            "name": source_doc_one.filename,
        },
    ]
    source_skill.save(update_fields=["context_hints"])

    client.force_login(copier)

    response = client.post(
        reverse("chat_next:copy_skill", args=[chat.id, source_skill.id]),
        {"modal_back_url": reverse("chat_next:get_skills", args=[chat.id])},
    )

    assert response.status_code == 200
    copied_skill = Skill.objects.filter(owner=copier).exclude(id=source_skill.id).get()
    copied_skill.refresh_from_db()

    copied_docs = list(
        Document.objects.filter(data_source=copied_skill.data_source).order_by(
            "filename"
        )
    )
    folder_hint = next(
        hint for hint in copied_skill.context_hints if hint.get("type") == "folder"
    )
    document_hint = next(
        hint for hint in copied_skill.context_hints if hint.get("type") == "document"
    )

    assert copied_skill.data_source.library.is_skill_library is True
    assert copied_skill.data_source.library.created_by == copier
    assert [doc.filename for doc in copied_docs] == ["one.txt", "two.txt"]
    assert {doc.saved_file_id for doc in copied_docs} == {
        saved_file_one.id,
        saved_file_two.id,
    }
    assert int(folder_hint["id"]) == copied_skill.data_source.id
    assert int(folder_hint["parent_library_id"]) == copied_skill.data_source.library_id
    assert document_hint["name"] == "one.txt"
    assert int(document_hint["id"]) in {doc.id for doc in copied_docs}
    assert int(document_hint["id"]) != source_doc_one.id
    assert Document.objects.filter(id=source_doc_one.id).exists()
    assert Document.objects.filter(id=source_doc_two.id).exists()


@pytest.mark.django_db
def test_copy_skill_keeps_non_skill_folder_hints_pointing_to_original_folder(
    client, all_apps_user
):
    owner = all_apps_user("copy-regular-folder-owner")
    copier = all_apps_user("copy-regular-folder-user")
    chat = Chat.objects.create(user=copier, title="Copy regular folder chat")
    library = owner.personal_library or owner.create_personal_library()
    data_source = DataSource.objects.create(
        library=library,
        name="Shared reference folder",
    )
    source_skill = Skill.objects.create(
        display_name_en="Reference source skill",
        description_en="Uses a regular folder reference",
        body_en="Prompt",
        owner=owner,
        sharing_option="everyone",
        context_hints=[
            {
                "type": "folder",
                "id": str(data_source.id),
                "name": data_source.name,
                "parent_library_id": str(library.id),
            }
        ],
    )

    client.force_login(copier)

    response = client.post(
        reverse("chat_next:copy_skill", args=[chat.id, source_skill.id]),
        {"modal_back_url": reverse("chat_next:get_skills", args=[chat.id])},
    )

    assert response.status_code == 200
    copied_skill = Skill.objects.filter(owner=copier).exclude(id=source_skill.id).get()

    assert copied_skill.context_hints == source_skill.context_hints
    assert not DataSource.objects.filter(skill=copied_skill).exists()


@pytest.mark.django_db
def test_copy_skill_always_clones_individual_document_hints_even_with_existing_access(
    client, all_apps_user
):
    owner = all_apps_user("copy-public-doc-owner")
    copier = all_apps_user("copy-public-doc-user")
    chat = Chat.objects.create(user=copier, title="Copy public doc chat")
    library = Library.objects.create(
        name="Public source library",
        created_by=owner,
        is_public=True,
    )
    data_source = DataSource.objects.create(
        library=library,
        name="Public docs folder",
    )
    saved_file = SavedFile.objects.create(openai_file_id="file-copy-public-doc")
    source_doc = Document.objects.create(
        data_source=data_source,
        saved_file=saved_file,
        filename="public-source.txt",
        extracted_text="public doc",
        status="SUCCESS",
        provenance=Document.PROVENANCE_USER_UPLOAD,
    )
    source_skill = Skill.objects.create(
        display_name_en="Public document source skill",
        description_en="Uses a public individual file hint",
        body_en="Prompt",
        owner=owner,
        sharing_option="everyone",
        context_hints=[
            {"type": "document", "id": source_doc.id, "name": source_doc.filename}
        ],
    )

    client.force_login(copier)

    response = client.post(
        reverse("chat_next:copy_skill", args=[chat.id, source_skill.id]),
        {"modal_back_url": reverse("chat_next:get_skills", args=[chat.id])},
    )

    assert response.status_code == 200
    copied_skill = Skill.objects.filter(owner=copier).exclude(id=source_skill.id).get()
    copied_doc = Document.objects.get(data_source=copied_skill.data_source)
    document_hint = copied_skill.context_hints[0]

    assert copied_doc.filename == source_doc.filename
    assert copied_doc.saved_file_id == source_doc.saved_file_id
    assert document_hint["type"] == "document"
    assert int(document_hint["id"]) == copied_doc.id
    assert copied_doc.id != source_doc.id


@pytest.mark.django_db
def test_skill_usage_count_is_shown_in_views(client, all_apps_user):
    user = all_apps_user("usage-count-ui")
    chat = Chat.objects.create(user=user, title="Usage count chat")
    skill = Skill.objects.create(
        display_name="Usage Count Skill",
        description="Shows usage",
        body="Test body",
        owner=user,
        load_count=2034,
    )
    client.force_login(user)

    list_response = client.get(reverse("chat_next:get_skills", args=[chat.id]))
    assert list_response.status_code == 200
    assert "Used 2034 times" in list_response.content.decode()

    edit_response = client.get(
        reverse("chat_next:edit_skill", args=[chat.id, skill.id])
    )
    assert edit_response.status_code == 200
    assert "Usage Count Skill" in edit_response.content.decode()


@pytest.mark.django_db
def test_edit_skill_removing_tag_persists_with_unified_tags_field(
    client, all_apps_user
):
    user = all_apps_user("skills-tag-remove")
    chat = Chat.objects.create(user=user, title="Tag removal chat")
    skill = Skill.objects.create(
        display_name_en="Tag removal skill",
        description_en="Skill used for tag removal test",
        body_en="Test body",
        owner=user,
        sharing_option="private",
    )
    tag = SkillTag.objects.create(name="compliance", name_en="compliance")
    skill.skill_tags.add(tag)
    client.force_login(user)

    response = client.post(
        reverse("chat_next:edit_skill", args=[chat.id, skill.id]),
        {
            "display_name_en": skill.display_name_en,
            "display_name_fr": skill.display_name_fr or "",
            "description_en": skill.description_en,
            "description_fr": skill.description_fr or "",
            "body_en": skill.body_en,
            "body_fr": skill.body_fr or "",
            "sharing_option": skill.sharing_option,
            "skill_tags": "[]",
        },
    )

    assert response.status_code == 200
    skill.refresh_from_db()
    assert not skill.skill_tags.filter(id=tag.id).exists()


@pytest.mark.django_db
def test_create_skill_requires_complete_english_or_french_section(
    client, all_apps_user
):
    user = all_apps_user("skills-bilingual-required")
    chat = Chat.objects.create(user=user, title="Bilingual validation chat")
    client.force_login(user)

    response = client.post(
        reverse("chat_next:create_skill", args=[chat.id]),
        {
            "display_name_en": "Incomplete EN",
            "display_name_fr": "",
            "description_en": "",
            "description_fr": "",
            "body_en": "",
            "body_fr": "",
            "sharing_option": "private",
            "skill_tags": "[]",
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert (
        "Please complete Display name, Description, and Prompt in either English or French."
        in content
    )
    assert not Skill.objects.filter(
        owner=user, display_name_en="Incomplete EN"
    ).exists()


@pytest.mark.django_db
def test_create_skill_accepts_complete_french_section(client, all_apps_user):
    user = all_apps_user("skills-french-complete")
    chat = Chat.objects.create(user=user, title="French content chat")
    client.force_login(user)

    response = client.post(
        reverse("chat_next:create_skill", args=[chat.id]),
        {
            "display_name_en": "",
            "display_name_fr": "Compétence FR",
            "description_en": "",
            "description_fr": "Description française complète",
            "body_en": "",
            "body_fr": "Invite française complète.",
            "sharing_option": "private",
            "skill_tags": "[]",
        },
    )

    assert response.status_code == 200
    assert Skill.objects.filter(owner=user, display_name_fr="Compétence FR").exists()


@pytest.mark.django_db
def test_new_skill_form_shows_upload_controls_before_skill_exists(
    client, all_apps_user
):
    user = all_apps_user("skills-upload-on-create-ui")
    chat = Chat.objects.create(user=user, title="Upload on create chat")
    client.force_login(user)

    response = client.get(reverse("chat_next:create_skill", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="skill-upload-button"' in content
    assert 'id="skill-upload-form"' in content
    assert f'hx-post="{reverse("chat_next:create_skill", args=[chat.id])}"' in content
    assert 'name="skill_upload_flow" value="1"' in content
    assert "Otto will create the skill using the current form details first." in content


@pytest.mark.django_db
def test_new_skill_form_shows_skill_import_entry_point(client, all_apps_user):
    user = all_apps_user("skills-import-on-create-ui")
    chat = Chat.objects.create(user=user, title="Import on create chat")
    client.force_login(user)

    response = client.get(reverse("chat_next:create_skill", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Import SKILL.md or .zip" in content
    assert 'id="skill-import-form"' in content
    assert f'hx-post="{reverse("chat_next:import_skill", args=[chat.id])}"' in content


@pytest.mark.django_db
def test_import_skill_markdown_creates_private_disabled_skill_and_refine_button(
    client, all_apps_user
):
    user = all_apps_user("skills-import-markdown")
    chat = Chat.objects.create(user=user, title="Import markdown chat")
    Skill.objects.update_or_create(
        owner=None,
        display_name_en="Skill Creator",
        defaults={
            "display_name_en": "Skill Creator",
            "description_en": "Refine skills",
            "body_en": "Help refine skills.",
            "sharing_option": "everyone",
        },
    )
    client.force_login(user)

    upload = SimpleUploadedFile(
        "SKILL.md",
        (
            b"---\n"
            b"name: imported-analysis\n"
            b"description: Analyze imported material\n"
            b"---\n\n"
            b"Review the source material carefully."
        ),
        content_type="text/markdown",
    )

    response = client.post(
        reverse("chat_next:import_skill", args=[chat.id]),
        {"skill_import-skill_file": upload},
    )

    assert response.status_code == 200

    skill = Skill.objects.get(owner=user, display_name_en="Imported Analysis")
    settings = ChatSettings.objects.get(user=user)

    assert skill.sharing_option == "private"
    assert skill.display_name_en == "Imported Analysis"
    assert skill.description_en == "Analyze imported material"
    assert skill.body_en == "Review the source material carefully."
    assert not settings.enabled_skills.filter(id=skill.id).exists()

    content = response.content.decode()
    assert "Imported skills stay private and disabled" in content
    assert "Refine with Skill Creator" in content
    assert "may require adaptation for Otto" in content


@pytest.mark.django_db
def test_import_skill_zip_queues_bundle_document_and_shows_status(
    client, all_apps_user, monkeypatch
):
    user = all_apps_user("skills-import-zip")
    chat = Chat.objects.create(user=user, title="Import zip chat")
    Skill.objects.update_or_create(
        owner=None,
        display_name_en="Skill Creator",
        defaults={
            "display_name_en": "Skill Creator",
            "description_en": "Refine skills",
            "body_en": "Help refine skills.",
            "sharing_option": "everyone",
        },
    )
    client.force_login(user)

    bundle_bytes = BytesIO()
    with ZipFile(bundle_bytes, "w") as archive:
        archive.writestr(
            "translator/SKILL.md",
            "---\nname: bundle-translator\ndescription: Bundle import\n---\n\nUse the bundle files.",
        )
        archive.writestr("translator/references/guide.md", "Reference content")
        archive.writestr("translator/scripts/helper.py", "print('hello')")

    process_calls = []

    def fake_process(document):
        process_calls.append(document.id)

    monkeypatch.setattr("librarian.models.Document.process", fake_process)

    upload = SimpleUploadedFile(
        "translator.zip",
        bundle_bytes.getvalue(),
        content_type="application/zip",
    )

    response = client.post(
        reverse("chat_next:import_skill", args=[chat.id]),
        {"skill_import-skill_file": upload},
    )

    assert response.status_code == 200

    skill = Skill.objects.get(owner=user, display_name_en="Bundle Translator")
    settings = ChatSettings.objects.get(user=user)
    data_source = skill.data_source
    bundle_document = Document.objects.get(
        data_source=data_source, filename="translator.zip"
    )

    assert data_source.library.is_skill_library is True
    assert data_source.library.created_by == user
    assert bundle_document.provenance == Document.PROVENANCE_USER_UPLOAD
    assert process_calls == [bundle_document.id]
    assert not settings.enabled_skills.filter(id=skill.id).exists()
    assert any(
        hint.get("type") == "folder" and str(hint.get("id")) == str(data_source.id)
        for hint in skill.context_hints
    )

    content = response.content.decode()
    assert "Supporting files from the uploaded bundle are being imported" in content
    assert "Imported bundle files" in content
    assert "spinner-border" in content


@pytest.mark.django_db
def test_imported_skill_keeps_refine_button_after_edit_and_reopen(
    client, all_apps_user
):
    user = all_apps_user("skills-import-edit-refine")
    chat = Chat.objects.create(user=user, title="Import edit refine chat")
    Skill.objects.update_or_create(
        owner=None,
        display_name_en="Skill Creator",
        defaults={
            "display_name_en": "Skill Creator",
            "description_en": "Refine skills",
            "body_en": "Help refine skills.",
            "sharing_option": "everyone",
        },
    )
    client.force_login(user)

    upload = SimpleUploadedFile(
        "SKILL.md",
        (
            b"---\n"
            b"name: imported-editable\n"
            b"description: Analyze imported material\n"
            b"---\n\n"
            b"Review the source material carefully."
        ),
        content_type="text/markdown",
    )

    import_response = client.post(
        reverse("chat_next:import_skill", args=[chat.id]),
        {"skill_import-skill_file": upload},
    )

    assert import_response.status_code == 200
    skill = Skill.objects.get(owner=user, display_name_en="Imported Editable")

    edit_response = client.post(
        reverse("chat_next:edit_skill", args=[chat.id, skill.id]),
        {
            "display_name_en": "Imported Editable",
            "display_name_fr": "",
            "description_en": "Analyze imported material",
            "description_fr": "",
            "body_en": "Review the source material carefully.\n\nAdd one Otto-specific note.",
            "body_fr": "",
            "sharing_option": "private",
            "context_hints": "[]",
            "skill_tags": "[]",
        },
    )

    assert edit_response.status_code == 200
    edit_content = edit_response.content.decode()
    assert "Refine with Skill Creator" in edit_content
    assert "may require adaptation for Otto" in edit_content

    reopen_response = client.get(
        reverse("chat_next:edit_skill", args=[chat.id, skill.id])
    )

    assert reopen_response.status_code == 200
    reopen_content = reopen_response.content.decode()
    assert "Refine with Skill Creator" in reopen_content
    assert "may require adaptation for Otto" in reopen_content


@pytest.mark.django_db
def test_create_skill_upload_flow_creates_skill_and_files_in_one_step(
    client, all_apps_user, monkeypatch
):
    user = all_apps_user("skills-upload-on-create")
    chat = Chat.objects.create(user=user, title="Create with files chat")
    saved_file = SavedFile.objects.create(openai_file_id="file-new-skill-upload")
    client.force_login(user)

    monkeypatch.setattr("librarian.models.Document.process", lambda self: None)

    with (
        patch("chat_next._views.skills.UploadForm.is_valid", return_value=True),
        patch(
            "chat_next._views.skills.UploadForm.save",
            return_value=[{"filename": "brief.txt", "saved_file": saved_file}],
        ),
    ):
        response = client.post(
            reverse("chat_next:create_skill", args=[chat.id]),
            {
                "display_name_en": "Create and upload",
                "display_name_fr": "",
                "description_en": "One flow",
                "description_fr": "",
                "body_en": "Create the skill and keep the uploaded brief handy.",
                "body_fr": "",
                "sharing_option": "private",
                "context_hints": "[]",
                "skill_tags": "[]",
                "skill_upload_flow": "1",
            },
        )

    assert response.status_code == 200

    skill = Skill.objects.get(owner=user, display_name_en="Create and upload")
    data_source = skill.data_source
    document = Document.objects.get(data_source=data_source, filename="brief.txt")

    assert data_source.library.is_skill_library is True
    assert data_source.library.created_by == user
    assert document.saved_file_id == saved_file.id
    assert any(
        hint.get("type") == "folder" and str(hint.get("id")) == str(data_source.id)
        for hint in skill.context_hints
    )
    assert response.content.decode().count("Manage skill files") == 1


@pytest.mark.django_db
def test_create_skill_upload_flow_with_incomplete_content_creates_disabled_draft(
    client, all_apps_user, monkeypatch
):
    user = all_apps_user("skills-upload-draft-create")
    chat = Chat.objects.create(user=user, title="Draft with files chat")
    saved_file = SavedFile.objects.create(openai_file_id="file-draft-skill-upload")
    client.force_login(user)

    monkeypatch.setattr("librarian.models.Document.process", lambda self: None)

    with (
        patch("chat_next._views.skills.UploadForm.is_valid", return_value=True),
        patch(
            "chat_next._views.skills.UploadForm.save",
            return_value=[{"filename": "notes.txt", "saved_file": saved_file}],
        ),
    ):
        response = client.post(
            reverse("chat_next:create_skill", args=[chat.id]),
            {
                "display_name_en": "",
                "display_name_fr": "",
                "description_en": "",
                "description_fr": "",
                "body_en": "",
                "body_fr": "",
                "sharing_option": "private",
                "context_hints": "[]",
                "skill_tags": "[]",
                "skill_upload_flow": "1",
            },
        )

    assert response.status_code == 200

    skill = Skill.objects.get(owner=user)
    data_source = skill.data_source
    settings = ChatSettings.objects.get(user=user)

    assert skill.display_name == ""
    assert not settings.enabled_skills.filter(id=skill.id).exists()
    assert data_source.name == "Untitled skill"
    assert Document.objects.filter(
        data_source=data_source, filename="notes.txt"
    ).exists()
    assert any(
        hint.get("type") == "folder"
        and str(hint.get("id")) == str(data_source.id)
        and hint.get("name") == "Untitled skill"
        for hint in skill.context_hints
    )

    content = response.content.decode()
    assert "Untitled skill" in content
    assert "This draft stays disabled" in content
    assert (
        quote(reverse("chat_next:edit_skill", args=[chat.id, skill.id]), safe="")
        in content
    )
    assert (
        quote(reverse("chat_next:create_skill", args=[chat.id]), safe="") not in content
    )
    assert 'id="current-skill-enabled-toggle"' not in content


@pytest.mark.django_db
def test_existing_draft_upload_response_uses_editor_url_for_manage_skill_files_back_link(
    client, all_apps_user, monkeypatch
):
    user = all_apps_user("skills-draft-upload-back-link")
    chat = Chat.objects.create(user=user, title="Draft upload back link chat")
    skill = Skill.objects.create(
        display_name="",
        description="",
        body="",
        owner=user,
        sharing_option="private",
    )
    saved_file = SavedFile.objects.create(openai_file_id="file-draft-upload-back-link")
    client.force_login(user)

    monkeypatch.setattr("librarian.models.Document.process", lambda self: None)

    with (
        patch("chat_next._views.skills.UploadForm.is_valid", return_value=True),
        patch(
            "chat_next._views.skills.UploadForm.save",
            return_value=[{"filename": "draft-note.txt", "saved_file": saved_file}],
        ),
    ):
        response = client.post(
            reverse("chat_next:skill_upload", args=[chat.id, skill.id]),
            {
                "modal_back_url": reverse("chat_next:get_skills", args=[chat.id]),
            },
        )

    assert response.status_code == 200
    content = response.content.decode()
    assert (
        quote(reverse("chat_next:edit_skill", args=[chat.id, skill.id]), safe="")
        in content
    )
    assert (
        quote(reverse("chat_next:skill_upload", args=[chat.id, skill.id]), safe="")
        not in content
    )


@pytest.mark.django_db
def test_skills_browser_shows_untitled_fallback_for_blank_draft_skills(
    client, all_apps_user
):
    user = all_apps_user("skills-draft-browser-fallback")
    chat = Chat.objects.create(user=user, title="Draft browser chat")
    Skill.objects.create(
        display_name="",
        description="",
        body="",
        owner=user,
        sharing_option="private",
    )
    client.force_login(user)

    response = client.get(reverse("chat_next:get_skills", args=[chat.id]))

    assert response.status_code == 200
    assert "Untitled skill" in response.content.decode()


@pytest.mark.django_db
def test_edit_skill_completing_draft_updates_folder_name(client, all_apps_user):
    user = all_apps_user("skills-draft-complete")
    chat = Chat.objects.create(user=user, title="Draft complete chat")
    draft_skill = Skill.objects.create(
        display_name="",
        description="",
        body="",
        owner=user,
        sharing_option="private",
        context_hints=[],
    )
    data_source = DataSource.objects.create(
        name="Untitled skill",
        library=user.skill_library or user.create_skill_library(),
        skill=draft_skill,
    )
    draft_skill.context_hints = [
        {"type": "folder", "id": str(data_source.id), "name": "Untitled skill"}
    ]
    draft_skill.save(update_fields=["context_hints"])
    client.force_login(user)

    response = client.post(
        reverse("chat_next:edit_skill", args=[chat.id, draft_skill.id]),
        {
            "display_name_en": "Finished draft",
            "display_name_fr": "",
            "description_en": "Now complete",
            "description_fr": "",
            "body_en": "This draft is now ready to use.",
            "body_fr": "",
            "sharing_option": "private",
            "context_hints": json.dumps(draft_skill.context_hints),
            "skill_tags": "[]",
        },
    )

    assert response.status_code == 200

    draft_skill.refresh_from_db()
    data_source.refresh_from_db()

    assert draft_skill.display_name_en == "Finished draft"
    assert data_source.name == "Finished draft"
    assert draft_skill.context_hints == [
        {"type": "folder", "id": str(data_source.id), "name": "Finished draft"}
    ]
    assert 'id="current-skill-enabled-toggle"' in response.content.decode()


@pytest.mark.django_db
def test_non_admin_can_make_skill_public(client, basic_user):
    """Beta: all users can share skills publicly."""
    user = basic_user("skills-public-restricted", accept_terms=True)
    chat = Chat.objects.create(user=user, title="Public restriction chat")
    client.force_login(user)

    response = client.post(
        reverse("chat_next:create_skill", args=[chat.id]),
        {
            "display_name_en": "Restricted skill",
            "display_name_fr": "",
            "description_en": "Valid description",
            "description_fr": "",
            "body_en": "Valid prompt",
            "body_fr": "",
            "sharing_option": "everyone",
            "skill_tags": "[]",
        },
    )

    assert response.status_code == 200
    assert Skill.objects.filter(
        owner=user, display_name_en="Restricted skill", sharing_option="everyone"
    ).exists()


@pytest.mark.django_db
def test_edit_skill_manage_uploaded_files_link_uses_modal_data_source_route(
    client, all_apps_user
):
    user = all_apps_user("skills-manage-files-link")
    chat = Chat.objects.create(user=user, title="Manage files chat")
    skill = Skill.objects.create(
        display_name="Manage files skill",
        description="Skill with uploads",
        body="Prompt",
        owner=user,
    )
    library = user.skill_library or user.create_skill_library()
    data_source = DataSource.objects.create(
        library=library,
        name="Skill folder",
        skill=skill,
    )
    client.force_login(user)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    content = response.content.decode()
    expected_url = reverse(
        "chat_next:modal_librarian_data_source", args=[chat.id, data_source.id]
    )
    assert expected_url in content
    assert f'hx-get="{expected_url}?modal_back_url=' in content
    assert 'hx-target="#chat-next-modal-content"' in content
    assert "window.openLibrarian" not in content
    assert f"/librarian/?data_source={data_source.id}" not in content
    assert "Manage skill files" in content


@pytest.mark.django_db
def test_view_only_shared_user_does_not_see_manage_uploaded_files_link(
    client, all_apps_user, basic_user
):
    owner = all_apps_user("skill-files-owner")
    viewer = basic_user("skill-files-viewer", accept_terms=True)
    chat = Chat.objects.create(user=viewer, title="Skill files viewer chat")
    skill = Skill.objects.create(
        display_name="Skill files view only",
        description="Shared read-only skill",
        body="Prompt",
        owner=owner,
        sharing_option="others",
    )
    skill.accessible_to.add(viewer)
    library = owner.skill_library or owner.create_skill_library()
    DataSource.objects.create(
        library=library,
        name="Owner skill folder",
        skill=skill,
    )
    client.force_login(viewer)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    assert "Manage skill files" not in response.content.decode()


@pytest.mark.django_db
def test_shared_skill_editor_can_open_manage_uploaded_files_modal(
    client, all_apps_user
):
    owner = all_apps_user("skill-files-editor-owner")
    editor = all_apps_user("skill-files-editor")
    chat = Chat.objects.create(user=editor, title="Skill files editor chat")
    skill = Skill.objects.create(
        display_name="Skill files editor",
        description="Shared editable skill",
        body="Prompt",
        owner=owner,
        sharing_option="others",
    )
    skill.editable_by.add(editor)
    library = owner.skill_library or owner.create_skill_library()
    data_source = DataSource.objects.create(
        library=library,
        name="Shared editable skill folder",
        skill=skill,
    )
    client.force_login(editor)

    edit_response = client.get(
        reverse("chat_next:edit_skill", args=[chat.id, skill.id])
    )
    assert edit_response.status_code == 200
    assert "Manage skill files" in edit_response.content.decode()

    modal_response = client.get(
        reverse(
            "chat_next:modal_librarian_data_source",
            args=[chat.id, data_source.id],
        )
    )

    assert modal_response.status_code == 200
    assert ">Libraries<" in modal_response.content.decode()


@pytest.mark.django_db
def test_shared_skill_editor_sees_owner_only_sharing_copy_without_visibility_radios(
    client, all_apps_user
):
    owner = all_apps_user("skill-sharing-owner")
    editor = all_apps_user("skill-sharing-editor")
    viewer = all_apps_user("skill-sharing-viewer")
    chat = Chat.objects.create(user=editor, title="Shared skill editor chat")
    skill = Skill.objects.create(
        display_name="Shared skill owner controls",
        description="Shared editable skill",
        body="Prompt",
        owner=owner,
        sharing_option="others",
    )
    skill.accessible_to.add(viewer)
    skill.editable_by.add(editor)
    client.force_login(editor)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert (
        "This skill was shared with you by another user. Only the skill owner can make it private or public."
        in content
    )
    assert "Make private" not in content
    assert "Share with specific people" not in content
    assert 'type="hidden" name="sharing_option" value="others"' in re.sub(
        "\s+", " ", content
    )
    assert "Share with:" in content
    assert "Allow these users to edit:" in content


@pytest.mark.django_db
def test_shared_skill_editor_post_cannot_change_sharing_mode(client, all_apps_user):
    owner = all_apps_user("skill-sharing-post-owner")
    editor = all_apps_user("skill-sharing-post-editor")
    chat = Chat.objects.create(user=editor, title="Shared skill post chat")
    skill = Skill.objects.create(
        display_name_en="Shared skill post controls",
        description_en="Original description",
        body_en="Original prompt",
        owner=owner,
        sharing_option="others",
    )
    skill.editable_by.add(editor)
    client.force_login(editor)

    response = client.post(
        reverse("chat_next:edit_skill", args=[chat.id, skill.id]),
        {
            "display_name_en": "Shared skill post controls",
            "display_name_fr": "",
            "description_en": "Editor updated description",
            "description_fr": "",
            "body_en": "Editor updated prompt",
            "body_fr": "",
            "sharing_option": "private",
            "context_hints": json.dumps(skill.context_hints),
            "skill_tags": "[]",
        },
    )

    assert response.status_code == 200

    skill.refresh_from_db()
    assert skill.description_en == "Editor updated description"
    assert skill.body_en == "Editor updated prompt"
    assert skill.sharing_option == "others"


@pytest.mark.django_db
def test_edit_public_skill_shows_editor_controls_without_share_with_field(
    client, all_apps_user
):
    owner = all_apps_user("public-skill-editor-controls-owner")
    editor = all_apps_user("public-skill-editor-controls-editor")
    chat = Chat.objects.create(user=owner, title="Public skill editor controls chat")
    skill = Skill.objects.create(
        display_name_en="Public editor controls",
        description_en="Public skill with named editors",
        body_en="Prompt",
        owner=owner,
        sharing_option="everyone",
    )
    skill.editable_by.add(editor)
    client.force_login(owner)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Allow these users to edit:" in content
    other_users_div = re.search(
        r'<div class="([^"]*)"\s+id="skill-other-users">', content
    )
    assert other_users_div is not None
    assert "d-none" not in other_users_div.group(1).split()
    share_with_div = re.search(
        r'<div id="skill-share-with-users"\s*(?:class="([^"]*)")?>', content
    )
    assert share_with_div is not None
    share_with_classes = (share_with_div.group(1) or "").split()
    assert "d-none" in share_with_classes


@pytest.mark.django_db
def test_edit_skill_shows_copy_url_button(client, all_apps_user):
    owner = all_apps_user("skill-copy-url-owner")
    chat = Chat.objects.create(user=owner, title="Skill copy url chat")
    skill = Skill.objects.create(
        display_name_en="Skill copy url",
        description_en="Skill with copy url control",
        body_en="Prompt",
        owner=owner,
        sharing_option="everyone",
    )
    client.force_login(owner)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Copy URL" in content
    assert (
        f'data-copy-url="{reverse("chat_next:new_chat")}?open_skill={skill.id}"'
        in content
    )


@pytest.mark.django_db
def test_edit_public_skill_persists_named_editors(client, all_apps_user):
    owner = all_apps_user("public-skill-editor-persist-owner")
    editor = all_apps_user("public-skill-editor-persist-editor")
    chat = Chat.objects.create(user=owner, title="Public skill editor persist chat")
    skill = Skill.objects.create(
        display_name_en="Public editor persist",
        description_en="Public skill",
        body_en="Prompt",
        owner=owner,
        sharing_option="everyone",
    )
    client.force_login(owner)

    response = client.post(
        reverse("chat_next:edit_skill", args=[chat.id, skill.id]),
        {
            "display_name_en": "Public editor persist",
            "display_name_fr": "",
            "description_en": "Public skill",
            "description_fr": "",
            "body_en": "Prompt updated",
            "body_fr": "",
            "sharing_option": "everyone",
            "editable_by": str(editor.id),
            "context_hints": json.dumps(skill.context_hints),
            "skill_tags": "[]",
        },
    )

    assert response.status_code == 200
    skill.refresh_from_db()
    assert skill.editable_by.filter(id=editor.id).exists()


@pytest.mark.django_db
def test_edit_skill_creates_notifications_for_new_direct_share_recipients(
    client, all_apps_user
):
    owner = all_apps_user("skill-share-notify-owner")
    viewer = all_apps_user("skill-share-notify-viewer")
    editor = all_apps_user("skill-share-notify-editor")
    chat = Chat.objects.create(user=owner, title="Skill share notify chat")
    skill = Skill.objects.create(
        display_name_en="Skill share notify",
        description_en="Notify direct recipients",
        body_en="Prompt",
        owner=owner,
        sharing_option="private",
    )
    client.force_login(owner)

    response = client.post(
        reverse("chat_next:edit_skill", args=[chat.id, skill.id]),
        {
            "display_name_en": "Skill share notify",
            "display_name_fr": "",
            "description_en": "Notify direct recipients",
            "description_fr": "",
            "body_en": "Prompt",
            "body_fr": "",
            "sharing_option": "others",
            "accessible_to": str(viewer.id),
            "editable_by": str(editor.id),
            "context_hints": json.dumps(skill.context_hints),
            "skill_tags": "[]",
        },
    )

    assert response.status_code == 200
    viewer_notification = Notification.objects.get(
        user=viewer, heading_en="Skill shared"
    )
    editor_notification = Notification.objects.get(
        user=editor, heading_en="Skill shared"
    )
    assert "with you." in viewer_notification.text_en
    assert "granted edit access" in editor_notification.text_en
    assert f"/chat_next/?open_skill={skill.id}" == viewer_notification.link
    assert f"/chat_next/?open_skill={skill.id}" == editor_notification.link
    assert (
        Notification.objects.filter(user=owner, heading_en="Skill shared").count() == 0
    )


@pytest.mark.django_db
def test_edit_skill_creates_notifications_for_new_team_share_recipients(
    client, all_apps_user
):
    owner = all_apps_user("skill-team-share-notify-owner")
    member = all_apps_user("skill-team-share-notify-member")
    chat = Chat.objects.create(user=owner, title="Skill team share notify chat")
    team = Team.objects.create(name="Policy editors", created_by=owner)
    TeamMembership.objects.create(team=team, user=owner, role="admin")
    TeamMembership.objects.create(team=team, user=member, role="member")
    skill = Skill.objects.create(
        display_name_en="Skill team share notify",
        description_en="Notify team recipients",
        body_en="Prompt",
        owner=owner,
        sharing_option="private",
    )
    client.force_login(owner)

    response = client.post(
        reverse("chat_next:edit_skill", args=[chat.id, skill.id]),
        {
            "display_name_en": "Skill team share notify",
            "display_name_fr": "",
            "description_en": "Notify team recipients",
            "description_fr": "",
            "body_en": "Prompt",
            "body_fr": "",
            "sharing_option": "others",
            "editable_by": f"team:{team.id}",
            "context_hints": json.dumps(skill.context_hints),
            "skill_tags": "[]",
        },
    )

    assert response.status_code == 200
    notification = Notification.objects.get(user=member, heading_en="Skill shared")
    assert 'with your team "Policy editors"' in notification.text_en
    assert "granted edit access" in notification.text_en


@pytest.mark.django_db
def test_edit_skill_making_skill_public_does_not_create_notifications(
    client, all_apps_user
):
    owner = all_apps_user("skill-public-no-notify-owner")
    chat = Chat.objects.create(user=owner, title="Skill public no notify chat")
    skill = Skill.objects.create(
        display_name_en="Skill public no notify",
        description_en="No public notifications",
        body_en="Prompt",
        owner=owner,
        sharing_option="private",
    )
    client.force_login(owner)

    response = client.post(
        reverse("chat_next:edit_skill", args=[chat.id, skill.id]),
        {
            "display_name_en": "Skill public no notify",
            "display_name_fr": "",
            "description_en": "No public notifications",
            "description_fr": "",
            "body_en": "Prompt",
            "body_fr": "",
            "sharing_option": "everyone",
            "context_hints": json.dumps(skill.context_hints),
            "skill_tags": "[]",
        },
    )

    assert response.status_code == 200
    assert Notification.objects.filter(heading_en="Skill shared").count() == 0


@pytest.mark.django_db
def test_view_only_shared_user_cannot_open_manage_uploaded_files_modal(
    client, all_apps_user, basic_user
):
    owner = all_apps_user("skill-files-owner-locked")
    viewer = basic_user("skill-files-viewer-locked", accept_terms=True)
    chat = Chat.objects.create(user=viewer, title="Skill files viewer locked chat")
    skill = Skill.objects.create(
        display_name="Skill files viewer locked",
        description="Shared read-only skill",
        body="Prompt",
        owner=owner,
        sharing_option="others",
    )
    skill.accessible_to.add(viewer)
    library = owner.skill_library or owner.create_skill_library()
    data_source = DataSource.objects.create(
        library=library,
        name="Locked skill folder",
        skill=skill,
    )
    client.force_login(viewer)

    response = client.get(
        reverse(
            "chat_next:modal_librarian_data_source",
            args=[chat.id, data_source.id],
        )
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_edit_skill_renders_shared_modal_header_with_back_button(client, all_apps_user):
    user = all_apps_user("skill-header-back")
    chat = Chat.objects.create(user=user, title="Skill header chat")
    skill = Skill.objects.create(
        display_name="Header skill",
        description="Header test",
        body="Prompt",
        owner=user,
    )
    client.force_login(user)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "chat-next-modal-back-button" in content
    assert reverse("chat_next:get_skills", args=[chat.id]) in content
    assert ">Skills<" in content
    assert 'id="skill-editor-title"' in content
    assert ">Header skill<" in content


@pytest.mark.django_db
def test_manage_uploaded_files_shared_modal_uses_libraries_header(
    client, all_apps_user
):
    user = all_apps_user("libraries-shared-header")
    chat = Chat.objects.create(user=user, title="Libraries header chat")
    skill = Skill.objects.create(
        display_name="Libraries header skill",
        description="Header test",
        body="Prompt",
        owner=user,
    )
    library = user.skill_library or user.create_skill_library()
    data_source = DataSource.objects.create(
        library=library,
        name="Skill folder",
        skill=skill,
    )
    client.force_login(user)

    response = client.get(
        reverse("chat_next:modal_librarian_data_source", args=[chat.id, data_source.id])
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert ">Libraries<" in content


@pytest.mark.django_db
def test_open_library_shared_modal_uses_libraries_header(client, all_apps_user):
    user = all_apps_user("libraries-open-library")
    chat = Chat.objects.create(user=user, title="Open library chat")
    library = user.personal_library
    client.force_login(user)

    response = client.get(
        reverse("chat_next:modal_librarian_library", args=[chat.id, library.id])
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert ">Libraries<" in content
    assert reverse("librarian:modal_view_library", args=[library.id]) in content


@pytest.mark.django_db
def test_edit_skill_shows_updated_sharing_copy_and_tags_below_sharing(
    client, all_apps_user
):
    user = all_apps_user("skill-sharing-copy")
    chat = Chat.objects.create(user=user, title="Sharing copy chat")
    skill = Skill.objects.create(
        display_name="Sharing copy skill",
        description="Sharing copy test",
        body="Prompt",
        owner=user,
    )
    client.force_login(user)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Share with specific people" in content
    assert "Sharing this skill may expose more information than intended." in content
    assert content.index('skill-form-section-title">Sharing</label>') < content.index(
        'skill-form-section-title">Tags</label>'
    )


@pytest.mark.django_db
def test_edit_skill_context_buttons_match_chat_input_order_and_tabs_use_librarian_style(
    client, all_apps_user
):
    user = all_apps_user("skill-context-controls")
    chat = Chat.objects.create(user=user, title="Context controls chat")
    skill = Skill.objects.create(
        display_name="Context controls skill",
        description="Context controls test",
        body="Prompt",
        owner=user,
    )
    client.force_login(user)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="skill-upload-button"' in content
    assert 'id="skill-context-button"' in content
    assert '<i class="bi bi-paperclip"></i><span class="ms-1">Upload</span>' in content
    assert content.index('id="skill-context-pills-container"') < content.index(
        'id="skill-upload-button"'
    )
    assert content.index('id="skill-upload-button"') < content.index(
        'id="skill-context-button"'
    )
    assert "preset-form-container" in content
    assert "language-content" in content
    assert 'id="skill-upload-progress-files"' in content
    assert 'id="skill-file-dropzone"' in content
    assert "supportDropArea: true" in content
    assert 'id="skill-upload-message"' in content
    assert 'class="message-blob w-100"' not in content
    assert "Files for this skill" not in content


@pytest.mark.django_db
def test_edit_skill_includes_broken_context_hint_statuses(client, all_apps_user):
    user = all_apps_user("skill-broken-hints")
    chat = Chat.objects.create(user=user, title="Broken hints chat")
    library = user.personal_library
    data_source = DataSource.objects.create(library=library, name="Deleted folder")
    broken_folder_id = data_source.id
    data_source.delete()
    skill = Skill.objects.create(
        display_name="Broken hints skill",
        description="Broken context test",
        body="Prompt",
        owner=user,
        context_hints=[
            {"type": "folder", "id": broken_folder_id, "name": "Deleted folder"},
        ],
    )
    client.force_login(user)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="skill-context-hint-statuses"' in content
    assert f'"folder:{broken_folder_id}"' in content
    assert "Broken link - please re-upload or re-select." in content
    assert 'bi-exclamation-circle"></i></span>' in content
    assert "if (isBrokenHint(h)) return false;" in content
    assert content.index('class="context-pill-remove"') < content.index(
        'class="context-pill-label"'
    )


@pytest.mark.django_db
def test_edit_skill_context_warning_uses_public_status_for_public_library_folder(
    client, all_apps_user
):
    user = all_apps_user("skill-public-folder-warning")
    chat = Chat.objects.create(user=user, title="Public folder warning chat")
    library = Library.objects.create(
        name="Public context library",
        created_by=user,
        is_public=True,
    )
    data_source = DataSource.objects.create(library=library, name="Public folder")
    skill = Skill.objects.create(
        display_name="Public folder warning skill",
        description="Public folder warning test",
        body="Prompt",
        owner=user,
        sharing_option="others",
        context_hints=[
            {"type": "folder", "id": data_source.id, "name": data_source.name},
        ],
    )
    client.force_login(user)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert (
        f'"folder:{data_source.id}": {{"broken": false, "message": "", "is_public": true}}'
        in content
    )
    assert "return !(status && status.is_public);" in content


@pytest.mark.django_db
def test_chat_message_context_pills_include_open_urls_for_librarian_items(
    client, all_apps_user
):
    user = all_apps_user("message-context-pill-links")
    chat = Chat.objects.create(user=user, title="Context pill links")
    skill = Skill.objects.create(
        display_name="Message context pill skill",
        description="Opens from a context pill",
        body="Prompt",
        owner=user,
    )
    library = user.personal_library
    data_source = DataSource.objects.create(library=library, name="Folder link")
    document = Document.objects.create(data_source=data_source, filename="file.txt")
    Message.objects.create(
        chat=chat,
        text="Context-linked message",
        is_bot=False,
        details={
            "context_hints": [
                {"type": "skill", "id": skill.id, "name": skill.display_name},
                {"type": "library", "id": library.id, "name": library.name},
                {"type": "folder", "id": data_source.id, "name": data_source.name},
                {"type": "document", "id": document.id, "name": document.filename},
                {"type": "tool", "id": "local_qa_libraries", "name": "Q&A"},
            ]
        },
    )
    client.force_login(user)

    response = client.get(reverse("chat_next:chat", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert (
        reverse("chat_next:modal_librarian_library", args=[chat.id, library.id])
        in content
    )
    assert reverse("chat_next:edit_skill", args=[chat.id, skill.id]) in content
    assert (
        reverse("chat_next:modal_librarian_data_source", args=[chat.id, data_source.id])
        in content
    )
    assert (
        reverse("chat_next:modal_librarian_document", args=[chat.id, document.id])
        in content
    )
    assert "context-pill-openable" in content
    assert "bi-lightbulb" in content


@pytest.mark.django_db
def test_chat_welcome_suggestions_disable_after_click(client, all_apps_user):
    user = all_apps_user("welcome-suggestions-disable")
    chat = Chat.objects.create(user=user, title="Welcome suggestions chat")
    client.force_login(user)

    response = client.get(reverse("chat_next:chat", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "function setDemoPromptButtonsDisabled(disabled)" in content
    assert "grid.querySelectorAll('.demo-prompt-btn')" in content
    assert "setDemoPromptButtonsDisabled(true);" in content


@pytest.mark.django_db
def test_librarian_modal_view_data_source_no_longer_renders_add_to_message_button(
    client, all_apps_user
):
    user = all_apps_user("librarian-no-add-to-message")
    library = user.personal_library
    data_source = DataSource.objects.create(library=library, name="Folder")
    client.force_login(user)

    response = client.get(
        reverse("librarian:modal_view_data_source", args=[data_source.id])
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Add to message" not in content
    assert "librarian-add-to-message" not in content


@pytest.mark.django_db
def test_edit_skill_post_stays_on_editor_instead_of_returning_browser(
    client, all_apps_user
):
    user = all_apps_user("skill-save-stays-put")
    chat = Chat.objects.create(user=user, title="Save skill chat")
    skill = Skill.objects.create(
        display_name_en="Save skill",
        description_en="Before",
        body_en="Prompt",
        owner=user,
        sharing_option="private",
    )
    client.force_login(user)

    response = client.post(
        reverse("chat_next:edit_skill", args=[chat.id, skill.id]),
        {
            "display_name_en": "Save skill",
            "display_name_fr": "",
            "description_en": "After",
            "description_fr": "",
            "body_en": "Prompt updated",
            "body_fr": "",
            "sharing_option": "private",
            "skill_tags": "[]",
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="skill-form"' in content
    assert 'id="skills-card-list"' not in content
    assert "Prompt updated" in content
    skill.refresh_from_db()
    assert skill.body_en == "Prompt updated"


@pytest.mark.django_db
def test_create_skill_post_stays_on_editor_after_create(client, all_apps_user):
    user = all_apps_user("skill-create-stays-put")
    chat = Chat.objects.create(user=user, title="Create skill chat")
    client.force_login(user)

    response = client.post(
        reverse("chat_next:create_skill", args=[chat.id]),
        {
            "display_name_en": "Created skill",
            "display_name_fr": "",
            "description_en": "Created description",
            "description_fr": "",
            "body_en": "Created prompt",
            "body_fr": "",
            "sharing_option": "private",
            "skill_tags": "[]",
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="skill-form"' in content
    assert 'id="skills-card-list"' not in content
    assert "Created skill" in content
    assert Skill.objects.filter(owner=user, display_name_en="Created skill").exists()


@pytest.mark.django_db
def test_create_skill_error_keeps_other_sharing_fields_visible(client, all_apps_user):
    user = all_apps_user("skills-sharing-others-create")
    chat = Chat.objects.create(user=user, title="Create sharing fields chat")
    client.force_login(user)

    response = client.post(
        reverse("chat_next:create_skill", args=[chat.id]),
        {
            "display_name_en": "Incomplete sharing form",
            "display_name_fr": "",
            "description_en": "",
            "description_fr": "",
            "body_en": "",
            "body_fr": "",
            "sharing_option": "others",
            "skill_tags": "[]",
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    other_users_div = re.search(
        r'<div class="([^"]*)"\s+id="skill-other-users">', content
    )
    assert other_users_div is not None
    assert "d-none" not in other_users_div.group(1).split()


@pytest.mark.django_db
def test_edit_skill_error_keeps_other_sharing_fields_visible(client, all_apps_user):
    user = all_apps_user("skills-sharing-others-edit")
    chat = Chat.objects.create(user=user, title="Edit sharing fields chat")
    skill = Skill.objects.create(
        display_name_en="Sharing others skill",
        description_en="Valid description",
        body_en="Valid prompt",
        owner=user,
        sharing_option="others",
    )
    client.force_login(user)

    response = client.post(
        reverse("chat_next:edit_skill", args=[chat.id, skill.id]),
        {
            "display_name_en": "Sharing others skill",
            "display_name_fr": "",
            "description_en": "Valid description",
            "description_fr": "",
            "body_en": "",
            "body_fr": "",
            "sharing_option": "others",
            "skill_tags": "[]",
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    other_users_div = re.search(
        r'<div class="([^"]*)"\s+id="skill-other-users">', content
    )
    assert other_users_div is not None
    assert "d-none" not in other_users_div.group(1).split()


@pytest.mark.django_db
def test_edit_skill_dirty_snapshot_includes_share_user_hidden_fields(
    client, all_apps_user
):
    user = all_apps_user("skills-sharing-dirty-snapshot")
    chat = Chat.objects.create(user=user, title="Dirty snapshot sharing chat")
    other_user = all_apps_user("skills-sharing-dirty-snapshot-other")
    skill = Skill.objects.create(
        display_name_en="Sharing dirty snapshot",
        description_en="Valid description",
        body_en="Valid prompt",
        owner=user,
        sharing_option="others",
    )
    skill.accessible_to.add(other_user)
    skill.editable_by.add(user)
    client.force_login(user)

    response = client.get(reverse("chat_next:edit_skill", args=[chat.id, skill.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "function addMultiValueHiddenField(name)" in content
    assert "addMultiValueHiddenField('accessible_to');" in content
    assert "addMultiValueHiddenField('editable_by');" in content


@pytest.mark.django_db
def test_edit_and_delete_buttons_for_user_messages(client, all_apps_user):
    user = all_apps_user("edit-delete-buttons-ui")
    chat = Chat.objects.create(user=user, title="Edit-delete chat")
    user_msg = Message.objects.create(chat=chat, text="Hello", is_bot=False)
    bot_msg = Message.objects.create(chat=chat, text="Hi there", is_bot=True)
    client.force_login(user)

    response = client.get(reverse("chat_next:chat", args=[chat.id]))

    assert response.status_code == 200
    content = response.content.decode()
    # Edit button present for user message
    assert (
        f'hx-get="{reverse("chat_next:edit_message", args=[user_msg.id])}?editing=1"'
        in content
    )
    # Delete button present for both messages
    assert (
        f'hx-delete="{reverse("chat_next:delete_message", args=[user_msg.id])}"'
        in content
    )
    assert (
        f'hx-delete="{reverse("chat_next:delete_message", args=[bot_msg.id])}"'
        in content
    )


@pytest.mark.django_db
def test_inline_editor_get_returns_edit_form(client, all_apps_user):
    user = all_apps_user("inline-editor-get")
    chat = Chat.objects.create(user=user, title="Inline editor chat")
    message = Message.objects.create(chat=chat, text="Original text", is_bot=False)
    client.force_login(user)

    response = client.get(
        reverse("chat_next:edit_message", args=[message.id]),
        {"editing": "1"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "message-inline-edit-form" in content
    assert "Original text" in content
    assert "Save and re-run" in content


@pytest.mark.django_db
def test_inline_editor_cancel_returns_normal_message(client, all_apps_user):
    user = all_apps_user("inline-editor-cancel")
    chat = Chat.objects.create(user=user, title="Cancel editor chat")
    message = Message.objects.create(chat=chat, text="Some text", is_bot=False)
    client.force_login(user)

    response = client.get(reverse("chat_next:edit_message", args=[message.id]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "message-inline-edit-form" not in content
    assert "Some text" in content


@pytest.mark.django_db
def test_edit_message_updates_original_text_and_reruns(client, all_apps_user):
    user = all_apps_user("edit-message-rerun")
    chat = Chat.objects.create(user=user, title="Edit rerun chat")
    settings, _ = ChatSettings.objects.get_or_create_for_user(user)
    settings.chat_model = "gpt-5.4-mini"
    settings.save(update_fields=["chat_model"])

    original = Message.objects.create(chat=chat, text="Old text", is_bot=False)
    stale_bot = Message.objects.create(
        chat=chat, text="Old response", is_bot=True, parent=original
    )
    stale_user = Message.objects.create(chat=chat, text="Follow-up", is_bot=False)
    client.force_login(user)

    response = client.post(
        reverse("chat_next:edit_message", args=[original.id]),
        {
            "user-message": "New edited text",
            "chat-model-selector-chat_model": "gpt-5.2",
            "chat-model-selector-chat_reasoning_effort": "high",
            "chat-model-selector-chat_verbosity": "high",
        },
    )

    assert response.status_code == 200

    original.refresh_from_db()
    assert original.text == "New edited text"
    assert not Message.objects.filter(id=stale_bot.id).exists()
    assert not Message.objects.filter(id=stale_user.id).exists()

    new_bot = Message.objects.filter(chat=chat, is_bot=True).get()
    assert new_bot.parent_id == original.id
    assert new_bot.text == ""
    assert new_bot.bot_name == "GPT-5.2"

    html = response.content.decode()
    assert "awaiting-response" in html
    assert f"id='message_{stale_bot.id}' hx-swap-oob='delete'" in html
    assert f"id='message_{stale_user.id}' hx-swap-oob='delete'" in html


@pytest.mark.django_db
def test_delete_message_removes_message(client, all_apps_user):
    user = all_apps_user("delete-message-chat-next")
    chat = Chat.objects.create(user=user, title="Delete chat")
    message = Message.objects.create(chat=chat, text="To be deleted", is_bot=False)
    client.force_login(user)

    response = client.delete(reverse("chat_next:delete_message", args=[message.id]))

    assert response.status_code == 200
    assert not Message.objects.filter(id=message.id).exists()
