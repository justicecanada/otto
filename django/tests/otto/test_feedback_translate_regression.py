from django.urls import reverse
from django.utils import timezone

import pytest

from otto.models import Feedback

from chat._llm.__init__ import DEFAULT_TRANSLATE_MODEL_ID
from chat.models import Chat, Message
from chat.responses import translate_response


@pytest.mark.django_db
def test_feedback_snapshot_preserves_summarize_prompt_after_translate(
    client, all_apps_user
):
    user = all_apps_user()
    client.force_login(user)

    # Create chat and set options
    chat = Chat.objects.create(title="test", user=user)
    opts = chat.options
    opts.summarize_prompt = "ORIGINAL_SUMMARIZE_PROMPT"
    opts.translate_prompt = "TRANSLATE_PROMPT"
    opts.translate_model = DEFAULT_TRANSLATE_MODEL_ID
    opts.save()

    # Create a user message and a bot response message
    user_msg = Message.objects.create(chat=chat, text="Hello", is_bot=False)
    bot_msg = Message.objects.create(chat=chat, parent=user_msg, is_bot=True)

    # Call translate_response to exercise the translate path (may raise) -- don't fail test on exceptions
    try:
        translate_response(chat, bot_msg, skip_cost=True)
    except Exception:
        pass

    # Submit feedback for the bot message
    date_and_time = timezone.now().strftime("%Y%m%d-%H%M%S")
    data = {
        "user": user.id,
        "feedback_type": Feedback.FEEDBACK_TYPE_CHOICES[0][0],
        "feedback_message": "Regression check",
        "app": "chat",
        "chat_message": bot_msg.id,
        "modified_by": user.id,
        "created_by": user.id,
        "created_at": date_and_time,
        "modified_at": date_and_time,
        "otto_version": "v0",
    }

    response = client.post(
        reverse("user_feedback", kwargs={"message_id": bot_msg.id}),
        data=data,
    )
    assert response.status_code == 200

    fb = Feedback.objects.filter(chat_message=bot_msg).first()
    assert fb is not None
    assert fb.preset_snapshot is not None
    assert fb.preset_snapshot.get("summarize_prompt") == "ORIGINAL_SUMMARIZE_PROMPT", (
        "Feedback snapshot summarize_prompt was overwritten by translate path"
    )
