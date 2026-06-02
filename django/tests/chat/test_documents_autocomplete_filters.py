from types import SimpleNamespace

from django.http import QueryDict

import pytest

from chat.forms import AdditionalDocumentsAutocomplete, ExcludedDocumentsAutocomplete
from librarian.models import DataSource, Document, Library


@pytest.mark.django_db
def test_excluded_documents_autocomplete_shows_only_selected_folder_documents(
    monkeypatch,
):
    library = Library.objects.create(name="Filter library")
    selected_folder = DataSource.objects.create(name="Selected", library=library)
    other_folder = DataSource.objects.create(name="Other", library=library)

    selected_doc = Document.objects.create(
        data_source=selected_folder,
        filename="selected-folder-doc.pdf",
    )
    Document.objects.create(
        data_source=other_folder,
        filename="other-folder-doc.pdf",
    )

    query = QueryDict("", mutable=True)
    query["library_id"] = str(library.id)
    query["selected_data_source_ids"] = str(selected_folder.id)

    monkeypatch.setattr(
        "chat.forms.get_request",
        lambda: SimpleNamespace(GET=query, META={}),
    )

    items = ExcludedDocumentsAutocomplete().get_items(search="")

    assert [item["value"] for item in items] == [str(selected_doc.id)]


@pytest.mark.django_db
def test_additional_documents_autocomplete_shows_only_non_selected_folder_documents(
    monkeypatch,
):
    library = Library.objects.create(name="Filter library")
    selected_folder = DataSource.objects.create(name="Selected", library=library)
    other_folder = DataSource.objects.create(name="Other", library=library)

    Document.objects.create(
        data_source=selected_folder,
        filename="selected-folder-doc.pdf",
    )
    other_doc = Document.objects.create(
        data_source=other_folder,
        filename="other-folder-doc.pdf",
    )

    query = QueryDict("", mutable=True)
    query["library_id"] = str(library.id)
    query["selected_data_source_ids"] = str(selected_folder.id)

    monkeypatch.setattr(
        "chat.forms.get_request",
        lambda: SimpleNamespace(GET=query, META={}),
    )

    items = AdditionalDocumentsAutocomplete().get_items(search="")

    assert [item["value"] for item in items] == [str(other_doc.id)]


def test_document_filter_autocomplete_placeholders_are_none():
    assert str(AdditionalDocumentsAutocomplete.placeholder) == "None"
    assert str(ExcludedDocumentsAutocomplete.placeholder) == "None"
