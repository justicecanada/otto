import urllib.parse

from django.conf import settings
from django.urls import reverse

import pytest

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

    assert "HX-Push-Url" in response
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
