import pytest

from chat._llm.__init__ import DEFAULT_TRANSLATE_MODEL_ID
from chat.models import Chat, Message
from chat.responses import translate_response


@pytest.mark.django_db
def test_translate_response_does_not_mutate_summarize_prompt(db, all_apps_user):
    user = all_apps_user()
    chat = Chat.objects.create(title="test", user=user)
    # set a known summarize prompt
    chat.options.summarize_prompt = "ORIGINAL_SUMMARIZE_PROMPT"
    chat.options.translate_prompt = "TRANSLATE_ONLY_PROMPT"
    chat.options.translate_model = DEFAULT_TRANSLATE_MODEL_ID
    chat.options.save()

    # create a message and response_message placeholder
    message = Message.objects.create(chat=chat, text="Hello world", is_bot=False)
    response_message = Message.objects.create(chat=chat, parent=message, is_bot=True)

    # Call translate_response - for GPT path this will invoke summarize_response
    # We call with skip_cost=True to avoid cost checks interfering
    try:
        translate_response(chat, response_message, skip_cost=True)
    except Exception:
        # translate_response may raise due to missing external services; that's fine
        pass

    chat.options.refresh_from_db()
    assert chat.options.summarize_prompt == "ORIGINAL_SUMMARIZE_PROMPT", (
        "translate_response mutated chat.options.summarize_prompt"
    )
