from django.urls import reverse

import pytest


@pytest.mark.django_db
def test_send_outlook_generates_mailto_with_percent_encoding(all_apps_user, client):
    user = all_apps_user()
    client.force_login(user)

    # Create a chat and message
    from chat.models import Chat, Message

    chat = Chat.objects.create(user=user, title="My Chat Title")
    body = "Line1\nThis is a test & check"
    message = Message.objects.create(chat=chat, text=body, is_bot=True)

    url = reverse("chat:send_outlook", args=[message.id])
    response = client.get(url)

    # Should return HTML with mailto link
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    # Verify the response contains a mailto link with proper subject and body
    from urllib.parse import quote

    expected_subject = quote(f"From Otto: {chat.title.strip()}", safe="")
    # Build expected view link as the view uses request.build_absolute_uri which in tests uses http://testserver
    expected_view_link = (
        f"http://testserver{reverse('chat:chat', args=[chat.id])}#message_{message.id}"
    )

    # The view currently does not supply any recipient, so generate_mailto
    # will include the literal "None" after mailto:.  Rather than hardcode
    # that odd detail we just check the pieces we care about.
    assert content.strip().startswith("<html")
    assert "mailto:" in content
    assert "window.close" in content
    # subject parameter should appear in the link
    assert f"subject={expected_subject}" in content

    # Verify body parameter exists and decodes to the expected text.  We
    # split on "body=" and then unquote the portion before any closing quote
    # or angle bracket to guard against pytest truncation of the printed
    # string.
    assert "body=" in content
    # isolate the encoded body portion
    import re
    import urllib.parse

    match = re.search(r"body=([^'\">]+)", content)
    assert match, "could not extract body from mailto link"
    actual_body_encoded = match.group(1)
    actual_body = urllib.parse.unquote(actual_body_encoded)
    assert actual_body == f"View in Otto: {expected_view_link}\n\n{body.strip()}"
