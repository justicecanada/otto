import urllib.parse

from django.conf import settings
from django.urls import reverse

import pytest

pytest_plugins = ("pytest_asyncio",)
skip_on_github_actions = pytest.mark.skipif(
    settings.IS_RUNNING_IN_GITHUB, reason="Skipping tests on GitHub Actions"
)

skip_on_devops_pipeline = pytest.mark.skipif(
    settings.IS_RUNNING_IN_DEVOPS, reason="Skipping tests on DevOps Pipelines"
)


@pytest.mark.django_db
def test_laws_index(client, all_apps_user):
    client.force_login(all_apps_user())
    response = client.get(reverse("laws:index"))
    assert response.status_code == 200
    assert "Legislation search" in response.content.decode()


@pytest.mark.django_db
def test_laws_search_and_answer(client, all_apps_user):
    client.force_login(all_apps_user())
    # Test basic search
    query = (
        "who has the right to access records about the defence of canada regulations?"
    )
    response = client.post(reverse("laws:search"), {"query": query})
    assert response.status_code == 200
    assert query in response.content.decode()

    assert "HX-Push-Url" in response
    result_url = response["HX-Push-Url"]
    result_uuid = result_url.split("/")[-1]
    assert result_uuid

    query = (
        "are the defence of canada regulations exempt from access to information act?"
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
@skip_on_github_actions
def test_laws_cache(client, all_apps_user):
    """Skipping on GitHub because requires Redis cache"""
    client.force_login(all_apps_user())
    # Test basic search
    query = (
        "who has the right to access records about the defence of canada regulations?"
    )
    response = client.post(reverse("laws:search"), {"query": query})
    assert response.status_code == 200
    assert query in response.content.decode()

    assert "HX-Push-Url" in response
    result_url = response["HX-Push-Url"]
    result_uuid = result_url.split("/")[-1]
    assert result_uuid

    # Test answer
    response = client.get(
        reverse("laws:answer", args=[str(result_uuid)]),
    )
    assert response.status_code == 200

    # Load an existing search by UUID
    response = client.get(
        reverse("laws:existing_search", args=[str(result_uuid)]),
    )
    assert response.status_code == 200
    assert query in response.content.decode()
