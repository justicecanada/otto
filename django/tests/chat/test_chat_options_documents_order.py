import pytest

from chat.forms import ChatOptionsForm
from chat.models import Chat
from librarian.models import Document


@pytest.mark.django_db
def test_qa_documents_initially_sorted_alphabetically(all_apps_user):
    user = all_apps_user()

    # Create a chat (ChatManager.create will also create ChatOptions and a personal DataSource)
    chat = Chat.objects.create(user=user)

    # Create documents in non-alphabetical order on the chat's data source
    doc_g = Document.objects.create(
        data_source=chat.data_source, manual_title="Gretzky"
    )
    doc_j = Document.objects.create(data_source=chat.data_source, filename="Jordan")
    doc_a = Document.objects.create(data_source=chat.data_source, manual_title="ayrton")

    # Associate them with the ChatOptions (preserve arbitrary insertion order)
    chat.options.qa_documents.set([doc_g.id, doc_j.id, doc_a.id])

    # Initialize the form and inspect the queryset used for the field
    form = ChatOptionsForm(instance=chat.options, user=user)
    qs_ids = list(form.fields["qa_documents"].queryset.values_list("id", flat=True))

    # Expect alphabetical order by name/title: ayrton, Jordan, Gretzky
    assert qs_ids == [doc_a.id, doc_g.id, doc_j.id]
