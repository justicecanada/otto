from django.conf import settings
from django.urls import reverse

import pytest

from chat.llm import OttoLLM
from chat.models import Chat, Message
from chat.utils import save_sources_and_update_security_label
from librarian.models import DataSource
from otto.models import SecurityLabel

pytest_plugins = ("pytest_asyncio",)

"""
Test:
- AnswerSource class (chat/models.py)
- message_sources view (chat/views.py)
- save_sources_and_update_security_label (chat/utils.py)

The test is based on the following scenario:
- Create a chat and a message
- Create a library and data source
- Set the data source security level to protected B
- Get some nodes using
    retriever = llm.get_retriever(library.uuid_hex)
    source_nodes = retriever.retrieve("query")
- Use save_sources_and_update_security_label(source_nodes, message, chat) to save the sources
- Check that the sources are saved correctly (AnswerSource objects are created)
- Check that the chat security label is updated correctly

Finally, test def message_sources(request, message_id)
This should return HTML that includes all the sources.
"""


@pytest.mark.django_db(databases=["default", "vector_db"])
def test_answer_sources(client, all_apps_user, load_example_pdf):
    from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters

    user = all_apps_user()
    llm = OttoLLM()
    chat = Chat.objects.create(user=user)
    query = "In example.pdf, what headings are there?"
    message = Message.objects.create(chat=chat, text=query)
    # Create a response message too
    response_message = Message.objects.create(
        chat=chat, text="bot response", is_bot=True, parent=message
    )
    # Get a data source that has an existing, loaded Document (see conftest.py)
    data_source = DataSource.objects.get(name_en="Wikipedia")
    library = data_source.library
    # Set the data source security label for testing later
    data_source.security_label = SecurityLabel.objects.get(acronym_en="PB")
    data_source.save()
    # Get some nodes from a fake query
    retriever = llm.get_retriever(
        library.uuid_hex,
        filters=MetadataFilters(
            filters=[MetadataFilter(key="node_type", value="document", operator="!=")]
        ),
    )
    source_nodes = retriever.retrieve(query)
    assert source_nodes
    # Save the sources and update the security label
    save_sources_and_update_security_label([source_nodes], response_message, chat)
    # Check that the sources are saved correctly
    response_message.refresh_from_db()
    assert response_message.sources.count() == len(source_nodes)
    # Check that the chat security label is updated correctly
    chat.refresh_from_db()
    assert chat.security_label == data_source.security_label

    # Test the message_sources view
    client.force_login(user)
    url = reverse("chat:message_sources", args=[response_message.id])
    response = client.get(url)
    assert response.status_code == 200
    # Check that there are some sources in the HTML
    html = response.content.decode("utf-8")
    assert html.count('div class="accordion-body') == len(source_nodes)
    # <page_1> tag should have been converted to "Page 1"
    assert "Page 1" in html
    # /wiki/Intentionally_blank_page should have been converted to a link
    # assert "https://en.wikipedia.org/wiki/Intentionally_blank_page" in html
    # print(html) #disabling this for now, as it is creating an html page from previous message from db
