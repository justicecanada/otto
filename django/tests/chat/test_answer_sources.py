from django.conf import settings
from django.urls import reverse

import pytest

from chat.llm import OttoLLM
from chat.models import Chat, Message
from chat.utils import save_sources
from librarian.models import DataSource

pytest_plugins = ("pytest_asyncio",)


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
    # Get some nodes from a fake query
    retriever = llm.get_retriever(
        library.uuid_hex,
        filters=MetadataFilters(
            filters=[MetadataFilter(key="node_type", value="document", operator="!=")]
        ),
    )
    source_nodes = retriever.retrieve(query)
    assert source_nodes
    # Save the sources
    save_sources([source_nodes], response_message, chat)
    # Check that the sources are saved correctly
    response_message.refresh_from_db()
    assert response_message.sources.count() == len(source_nodes)

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
