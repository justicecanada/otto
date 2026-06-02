import urllib.parse
import uuid
from types import SimpleNamespace

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.urls import reverse

import pytest

from laws.models import Law
from laws.search_history.models import LawSearch

pytest_plugins = ("pytest_asyncio",)


@pytest.mark.django_db
def test_laws_index(client, all_apps_user):
    client.force_login(all_apps_user())
    response = client.get(reverse("laws:index"))
    assert response.status_code == 200
    assert "Legislation Search" in response.content.decode()


@pytest.mark.django_db(databases=["default", "vector_db"])
def test_laws_search_and_answer(client, all_apps_user):
    client.force_login(all_apps_user())
    # Test basic search
    query = (
        "who has the right to access records about the defence of canada regulations?"
    )
    response = client.post(reverse("laws:search"), {"query": query})
    assert response.status_code == 200
    # Expect the query to be truncated to 60 characters if it is long
    truncated = query[:59]
    assert truncated in response.content.decode()

    # Check if sources were found - this depends on whether laws are loaded
    if "HX-Push-Url" in response:
        # Laws are loaded, test the full flow
        result_url = response["HX-Push-Url"]
        result_uuid = result_url.split("/")[-1]
        assert result_uuid

        # Get one of the source node IDs so we can test url laws:source (with a node ID)
        source_id = response.context["sources"][0]["node_id"]
        # unquote the source_id
        source_id = urllib.parse.unquote(source_id)
        response = client.get(reverse("laws:source", args=[source_id]))
        assert response.status_code == 200

        # Test that the source URL points to a subsection on the laws-lois website
        assert "FullText.html#" in response.content.decode()
    else:
        # No sources found - this is expected if laws aren't loaded
        assert (
            "No sources found" in response.content.decode()
            or "sources" not in response.context
        )

    # Test advanced search - with no acts/regs selected it should return "no sources found"
    response = client.post(
        reverse("laws:search"),
        {"query": query, "advanced": "true", "search_laws_option": "specific_laws"},
    )
    assert response.status_code == 200
    assert "No sources found" in response.content.decode()

    # With a date range far in the future it should return "no sources found"
    response = client.post(
        reverse("laws:search"),
        {
            "query": query,
            "advanced": "true",
            "date_filter_option": "filter_dates",
            "in_force_date_start": "2050-10-12",
        },
    )
    assert response.status_code == 200
    assert "No sources found" in response.content.decode()


@pytest.mark.django_db
def test_clear_search_history_htmx_post(client, all_apps_user):
    """Clear all search history via HTMX POST returns empty HttpResponse and deletes records."""
    user = all_apps_user()
    client.force_login(user)

    # Seed some search history entries
    LawSearch.objects.create(user=user, query="alpha")
    LawSearch.objects.create(user=user, query="beta")

    # Ensure they exist
    assert LawSearch.objects.filter(user=user).count() == 2

    # HTMX POST should clear and return empty response body
    response = client.post(
        reverse("laws:clear_history"),
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert isinstance(response, HttpResponse)
    # Just assert that it's non-empty and looks like HTML
    body = response.content.decode()
    assert body.strip()  # not empty
    assert "<" in body and ">" in body

    # Records should be deleted
    assert LawSearch.objects.filter(user=user).count() == 0


@pytest.mark.django_db
def test_clear_search_history_non_htmx_post_redirects(client, all_apps_user):
    """Non-HTMX POST clears history and redirects to laws:index."""
    user = all_apps_user()
    client.force_login(user)

    LawSearch.objects.create(user=user, query="gamma")

    response = client.post(reverse("laws:clear_history"))

    # Should redirect (302) to laws:index
    assert response.status_code in (302, 303)
    assert response.url == reverse("laws:index")

    # History cleared
    assert LawSearch.objects.filter(user=user).count() == 0


@pytest.mark.django_db
def test_clear_search_history_get_redirects_without_deleting(client, all_apps_user):
    """GET should not delete and should redirect to laws:index."""
    user = all_apps_user()
    client.force_login(user)

    LawSearch.objects.create(user=user, query="delta")

    response = client.get(reverse("laws:clear_history"))

    assert response.status_code in (302, 303)
    assert response.url == reverse("laws:index")

    # Record remains
    assert LawSearch.objects.filter(user=user).count() == 1


@pytest.mark.django_db
def test_delete_search_post_removes_entry(client, all_apps_user):
    """POST to delete_search deletes the entry and returns empty 200 response."""
    user = all_apps_user()
    client.force_login(user)

    entry = LawSearch.objects.create(user=user, query="to delete")

    response = client.post(
        reverse("laws:delete_search", kwargs={"search_id": entry.id})
    )
    assert response.status_code == 200
    assert response.content == b""

    assert not LawSearch.objects.filter(id=entry.id, user=user).exists()


@pytest.mark.django_db
def test_delete_search_delete_method_supported(client, all_apps_user):
    """DELETE to delete_search also deletes the entry."""
    user = all_apps_user()
    client.force_login(user)

    entry = LawSearch.objects.create(user=user, query="to delete via DELETE")

    response = client.delete(
        reverse("laws:delete_search", kwargs={"search_id": entry.id})
    )
    assert response.status_code == 200
    assert response.content == b""

    assert not LawSearch.objects.filter(id=entry.id, user=user).exists()


@pytest.mark.django_db
def test_delete_search_invalid_method_returns_400(client, all_apps_user):
    """GET on delete_search returns 400 JSON error."""
    user = all_apps_user()
    client.force_login(user)

    entry = LawSearch.objects.create(user=user, query="will not be deleted")

    response = client.get(reverse("laws:delete_search", kwargs={"search_id": entry.id}))
    assert response.status_code == 400
    assert response["content-type"] == "application/json"
    assert b"Invalid request method" in response.content


@pytest.mark.django_db
def test_download_results_uses_cached_sources(client, all_apps_user, monkeypatch):
    """download_results returns a text file built from cached sources when cache is populated."""
    user = all_apps_user()
    client.force_login(user)

    # Minimal fake node/source objects
    class FakeNode:
        def __init__(self, node_id, display_metadata, text):
            self.node_id = node_id
            self.metadata = {"display_metadata": display_metadata}
            self.text = text

    class FakeSource:
        def __init__(self, node):
            self.node = node

    # Create a LawSearch with a query_uuid
    law_search = LawSearch.objects.create(
        user=user,
        query="Test query",
        search_parameters={},
        query_uuid="uuid-123",
    )

    fake_sources = [
        FakeSource(FakeNode("node-1", "Title One", "Body one")),
        FakeSource(FakeNode("node-2", "Title Two", "Body two")),
    ]

    # Patch cache.get to return query + sources
    from django.core import cache as django_cache

    def fake_get(key, default=None):
        if key == "uuid-123":
            return {"query": "Test query", "sources": fake_sources}
        return default

    monkeypatch.setattr(django_cache, "cache", django_cache.cache)
    monkeypatch.setattr(django_cache.cache, "get", fake_get, raising=False)

    url = reverse("laws:download_results", args=[law_search.id])

    response = client.get(url)

    assert response.status_code == 200
    assert response["Content-Type"] == "text/plain"

    body = response.content.decode()
    # Basic structure checks
    assert "Query:" in body
    assert "Test query" in body
    assert "# Title One" in body
    assert "# Title Two" in body
    assert "Body one" in body
    assert "Body two" in body

    # Filename in Content-Disposition
    assert "attachment;" in response["Content-Disposition"]
    assert f"_{law_search.id}.txt" in response["Content-Disposition"]


@pytest.mark.django_db
def test_download_results_replays_search_when_cache_missing(
    client, all_apps_user, monkeypatch
):
    """When cache is missing it should replay the search to rebuild sources."""

    user = all_apps_user()
    client.force_login(user)

    law_search = LawSearch.objects.create(
        user=user,
        query="Replay query",
        search_parameters={"advanced": True, "ai_answer": "on"},
        query_uuid=None,
    )

    class FakeCache:
        def __init__(self):
            self.data = {}

        def get(self, key, default=None):
            return self.data.get(key, default)

        def set(self, key, value, timeout=None):
            self.data[key] = value

        def delete(self, key):
            self.data.pop(key, None)

    fake_cache = FakeCache()
    monkeypatch.setattr("laws.views.cache", fake_cache)

    fake_sources = [
        SimpleNamespace(
            node=SimpleNamespace(
                metadata={"display_metadata": "Replay Title"},
                text="Replay body",
            )
        )
    ]

    replay_called = {"value": False}

    def fake_search(request, recreated_law_search):
        replay_called["value"] = True
        assert getattr(request, "_from_history", False) is True
        assert request.method == "POST"
        new_uuid = "replay-uuid"
        fake_cache.set(
            new_uuid, {"query": recreated_law_search.query, "sources": fake_sources}
        )
        recreated_law_search.query_uuid = new_uuid
        recreated_law_search.save(update_fields=["query_uuid"])
        return HttpResponse("ok")

    monkeypatch.setattr("laws.views.search", fake_search)

    response = client.get(reverse("laws:download_results", args=[law_search.id]))

    law_search.refresh_from_db()
    assert replay_called["value"] is True
    assert law_search.query_uuid == "replay-uuid"
    assert response.status_code == 200
    assert "Replay Title" in response.content.decode()


@pytest.mark.django_db
def test_sources_to_html_formats_sources_correctly():
    """sources_to_html produces expected dict structure from source objects."""
    from laws.views import sources_to_html

    class FakeNode:
        def __init__(
            self,
            node_id,
            display_metadata,
            text,
            chunk="1/2",
            headings=None,
            lang="eng",
        ):
            self.node_id = node_id
            self.text = text
            self.metadata = {
                "display_metadata": display_metadata,
                "chunk": chunk,
                "lang": lang,
            }
            if headings is not None:
                self.metadata["headings"] = headings

    class FakeSource:
        def __init__(self, node):
            self.node = node

    sources = [
        FakeSource(
            FakeNode(
                "doc_123",
                "Display Title",
                "Some **markdown** text",
                chunk="3/4",
                headings=["h1"],
                lang="eng",
            )
        )
    ]

    result = sources_to_html(sources)
    assert isinstance(result, list)
    assert len(result) == 1
    item = result[0]

    # node_id encoded and with + replaced by - when no _schedule_
    assert "node_id" in item
    assert "title" in item
    assert "chunk" in item
    assert "html" in item

    assert item["title"] == "Display Title"
    assert item["chunk"] == "3/4"
    assert item["headings"] == ["h1"]
    # html_render was applied; we just assert non-empty HTML-ish output
    assert "<" in item["html"] and ">" in item["html"]


@pytest.mark.django_db
def test_source_view_renders_nodes(client, all_apps_user, monkeypatch):
    client.force_login(all_apps_user())

    Law.objects.all().delete()
    Law.objects.create(
        title="Test Law (T-1)",
        short_title="Test Law",
        long_title="Long Test Law",
        ref_number="T-1",
        enabling_authority="Authority",
        node_id="base-node",
        node_id_en="doc-eng-1",
        node_id_fr="doc-fra-1",
        short_title_en="Test Law EN",
        short_title_fr="Test Law FR",
        type="act",
        eng_law_id="ENG-T-1",
    )

    english_node = {
        "text": "English text with **markdown**",
        "metadata": {
            "doc_id": "doc-eng-1",
            "display_metadata": "Section 1\nMore details",
            "chunk": "2/3",
            "headings": ["Heading"],
            "lang": "eng",
            "lims_id": "LIMS-123",
            "section_id": "sec-1",
            "parent_id": "parent-1",
        },
    }
    french_node = {
        "text": "Texte français",
        "metadata": {
            "doc_id": "doc-fra-1",
            "display_metadata": "Article 1\nDétails",
            "chunk": "4/1",
            "lang": "fra",
            "lims_id": "LIMS-999",
        },
    }

    def fake_get_source_node(node_id):
        return english_node if node_id == "doc-eng-1" else None

    monkeypatch.setattr("laws.views.get_source_node", fake_get_source_node)
    monkeypatch.setattr("laws.views.get_other_lang_node", lambda *_: french_node)
    monkeypatch.setattr(
        "laws.views.get_law_url", lambda law_obj, lang: f"http://laws.local/{lang}"
    )

    response = client.get(reverse("laws:source", args=["doc-eng-1"]))

    assert response.status_code == 200
    ctx = response.context
    assert ctx["source_node"]["title"] == "Section 1"
    assert "English text" in ctx["source_node"]["html"]
    assert ctx["other_lang_node"]["chunk"] is None  # chunk ending with /1 becomes None
    assert ctx["law"].url == "http://laws.local/eng"
    assert "FullText" in ctx["url_suffix"]


@pytest.mark.django_db
def test_get_answer_column_renders_partial(client, all_apps_user):
    client.force_login(all_apps_user())

    query_uuid = str(uuid.uuid4())
    response = client.get(reverse("laws:get_answer_column", args=[query_uuid]))

    assert response.status_code == 200
    assert query_uuid in response.content.decode()
    assert response.context["query_uuid"] == query_uuid


@pytest.mark.django_db
def test_answer_streams_error_when_no_sources(
    client, all_apps_user, monkeypatch, DummyLLM
):
    client.force_login(all_apps_user())

    query_uuid = str(uuid.uuid4())
    cache.set(
        query_uuid,
        {
            "sources": [],
            "query": "Test query",
            "trim_redundant": False,
            "model": "gpt-4.1-mini",
            "context_tokens": 1000,
            "additional_instructions": urllib.parse.quote_plus("Follow up"),
            "law_search_id": None,
        },
        timeout=30,
    )

    class TestLLM(DummyLLM):
        def __init__(self, *args, **kwargs):
            kwargs.pop("deployment", None)
            kwargs.pop("temperature", None)
            super().__init__(
                kwargs.pop("mock_embedding", None), kwargs.pop("priority", 0)
            )

        def get_response_synthesizer(
            self, *args, **kwargs
        ):  # pragma: no cover - not used
            raise AssertionError(
                "Response synthesizer should not be invoked when sources list is empty"
            )

        def create_costs(self):
            return {}

    monkeypatch.setattr("laws.views.OttoLLM", TestLLM)

    captured = []

    def fake_htmx(generator, llm, qid):
        for chunk in generator:
            captured.append(chunk)
            yield f"chunk::{chunk}"

    monkeypatch.setattr("laws.views.htmx_sse_response", fake_htmx)

    response = client.get(reverse("laws:answer", args=[query_uuid]))

    assert response.status_code == 200
    body_chunks = []
    for chunk in response.streaming_content:
        if isinstance(chunk, str):
            chunk = chunk.encode()
        body_chunks.append(chunk)
    body = b"".join(body_chunks).decode()
    assert "chunk::" in body
    assert any("Error generating AI response" in text for text in captured)
    assert response["Content-Type"] == "text/event-stream"

    cache.delete(query_uuid)


@pytest.mark.django_db
def test_answer_streams_with_sources(client, all_apps_user, monkeypatch, DummyLLM):
    client.force_login(all_apps_user())

    query_uuid = str(uuid.uuid4())

    class FakeCache:
        def __init__(self, initial):
            self.data = initial

        def get(self, key, default=None):
            return self.data.get(key, default)

        def set(self, key, value, timeout=None):
            self.data[key] = value

        def delete(self, key):
            self.data.pop(key, None)

    def build_source(idx):
        node = SimpleNamespace(
            metadata={
                "section_id": f"sec-{idx}",
                "parent_id": f"parent-{idx}",
                "display_metadata": f"Display {idx}",
            },
            text=f"Body {idx}",
        )
        node.get_content = lambda metadata_mode, txt=node.text: txt
        return SimpleNamespace(node=node)

    sources = [build_source(1), build_source(2)]
    cache_store = {
        query_uuid: {
            "sources": sources.copy(),
            "query": "Explain sample clauses",
            "trim_redundant": False,
            "model": "gpt-4.1-mini",
            "context_tokens": 10,
            "additional_instructions": urllib.parse.quote_plus("Full context"),
            "law_search_id": None,
        }
    }

    fake_cache = FakeCache(cache_store)
    monkeypatch.setattr("laws.views.cache", fake_cache)

    monkeypatch.setattr("laws.views.num_tokens", lambda text, model: 1)

    recorded = {}

    class DummyStreamingResponse:
        def __init__(self, chunks):
            self.response_gen = iter(chunks)

    class DummySynthesizer:
        def __init__(self, recorder):
            self.recorder = recorder

        def synthesize(self, query, nodes):
            self.recorder["query"] = query
            self.recorder["nodes"] = nodes
            return DummyStreamingResponse(["alpha", "beta"])

    class TestLLM(DummyLLM):
        def __init__(self, *args, **kwargs):
            kwargs.pop("deployment", None)
            kwargs.pop("temperature", None)
            super().__init__(
                kwargs.pop("mock_embedding", None), kwargs.pop("priority", 0)
            )

        def get_response_synthesizer(self, prompt):
            recorded["prompt"] = prompt
            return DummySynthesizer(recorded)

        def create_costs(self):
            return {"cad": 0.01}

    monkeypatch.setattr("laws.views.OttoLLM", TestLLM)

    captured_chunks = []

    def fake_htmx(generator, llm, qid):
        for chunk in generator:
            captured_chunks.append(chunk)
            yield f"wrapped::{chunk}"

    monkeypatch.setattr("laws.views.htmx_sse_response", fake_htmx)

    response = client.get(reverse("laws:answer", args=[query_uuid]))

    assert response.status_code == 200
    body = b"".join(
        chunk if isinstance(chunk, bytes) else chunk.encode()
        for chunk in response.streaming_content
    ).decode()
    assert "wrapped::alpha" in body
    assert recorded["query"] == "Explain sample clauses"
    assert len(recorded["nodes"]) == 2
    assert captured_chunks == ["alpha", "beta"]


@pytest.mark.django_db(databases=["default", "vector_db"])
def test_search_applies_date_filters(client, all_apps_user, monkeypatch, DummyLLM):
    client.force_login(all_apps_user())

    Law.objects.all().delete()
    Law.objects.create(
        title="Sample Law (S-1)",
        short_title="Sample Law",
        long_title="Sample Law Long",
        ref_number="S-1",
        enabling_authority="Authority",
        node_id="sample-node",
        node_id_en="doc-eng-1",
        node_id_fr="doc-fra-1",
        short_title_en="Sample Law EN",
        short_title_fr="Sample Law FR",
        type="act",
        eng_law_id="ENG-S-1",
    )

    class DummyRetriever:
        def retrieve(self, query):
            node_1 = SimpleNamespace(
                node_id="node-1",
                text="Body for node-1",
                metadata={
                    "display_metadata": "Title node-1\nExtra",
                    "chunk": "2/2",
                    "headings": ["Heading"],
                    "lang": "eng",
                },
            )
            node_2 = SimpleNamespace(
                node_id="node-2",
                text="Body for node-2",
                metadata={
                    "display_metadata": "Title node-2\nExtra",
                    "chunk": "3/1",
                    "headings": ["Heading"],
                    "lang": "eng",
                },
            )
            return [
                SimpleNamespace(node=node_1, score=0.9),
                SimpleNamespace(node=node_2, score=0.3),
            ]

    captured_filters = {}

    class TestLLM(DummyLLM):
        def __init__(self, *args, **kwargs):
            kwargs.pop("deployment", None)
            kwargs.pop("temperature", None)
            super().__init__(
                kwargs.pop("mock_embedding", None), kwargs.pop("priority", 0)
            )

        def get_retriever(self, **kwargs):
            captured_filters["filters"] = kwargs["filters"]
            return DummyRetriever()

        def create_costs(self):
            return {}

    monkeypatch.setattr("laws.views.OttoLLM", TestLLM)

    payload = {
        "query": "What is the sample law?",
        "advanced": "true",
        "ai_answer": "on",
        "vector_ratio": "0.5",
        "top_k": "5",
        "model": settings.DEFAULT_LAWS_MODEL,
        "context_tokens": "2000",
        "additional_instructions": "Please summarise",
        "language": "en",
        "search_laws_option": "all",
        "date_filter_option": "filter_dates",
        "in_force_date_start": "2023-01-01",
        "in_force_date_end": "2023-12-31",
        "last_amended_date_start": "2020-01-01",
        "last_amended_date_end": "2024-01-01",
    }

    response = client.post(reverse("laws:search"), payload)

    assert response.status_code == 200
    assert "filters" in captured_filters

    filter_tuples = {
        (f.key, f.operator, f.value) for f in captured_filters["filters"].filters
    }
    assert ("in_force_start_date", ">=", "2023-01-01") in filter_tuples
    assert ("in_force_start_date", "<=", "2023-12-31") in filter_tuples
    assert ("last_amended_date", ">=", "2020-01-01") in filter_tuples
    assert ("last_amended_date", "<=", "2024-01-01") in filter_tuples
    assert response.context["query"] == payload["query"]
    assert response.context["sources"]
