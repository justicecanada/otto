from django.urls import reverse

import pytest
from chat_next.models import SkillTag


@pytest.mark.django_db
def test_manage_skill_tags_shows_add_tag_button_and_modal(client, all_apps_user):
    admin_user = all_apps_user()
    client.force_login(admin_user)

    response = client.get(reverse("manage_skill_tags"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Add tag" in content
    assert 'id="add-tag-modal"' in content
    assert reverse("add_skill_tag") in content


@pytest.mark.django_db
def test_add_skill_tag_creates_bilingual_tag(client, all_apps_user):
    admin_user = all_apps_user()
    client.force_login(admin_user)

    response = client.post(
        reverse("add_skill_tag"),
        data={
            "name_en": "Legal Ops",
            "name_fr": "Opérations juridiques",
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("manage_skill_tags")

    tag = SkillTag.objects.get(name_en="legal ops")
    assert tag.name_fr == "opérations juridiques"
    assert tag.embedding is not None


@pytest.mark.django_db
def test_add_skill_tag_blocks_case_insensitive_duplicates(client, all_apps_user):
    admin_user = all_apps_user()
    client.force_login(admin_user)

    SkillTag.objects.create(
        name="translation", name_en="translation", name_fr="traduction"
    )

    response = client.post(
        reverse("add_skill_tag"),
        data={
            "name_en": "Translation",
            "name_fr": "Traduction",
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("manage_skill_tags")
    assert (
        SkillTag.objects.filter(name_en="translation", name_fr="traduction").count()
        == 1
    )


@pytest.mark.django_db
def test_manage_skill_tags_shows_two_keep_buttons_for_merge(client, all_apps_user):
    admin_user = all_apps_user()
    client.force_login(admin_user)

    SkillTag.objects.create(name="legal", name_en="legal", name_fr="juridique")
    SkillTag.objects.create(name="legel", name_en="legel", name_fr="juridiqe")

    response = client.get(reverse("manage_skill_tags"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Merge tags" in content
    assert 'Keep "legal / juridique"' in content
    assert 'Keep "legel / juridiqe"' in content


@pytest.mark.django_db
def test_manage_skill_tags_shows_manual_merge_form(client, all_apps_user):
    admin_user = all_apps_user()
    client.force_login(admin_user)

    SkillTag.objects.create(name="alpha", name_en="alpha", name_fr="alpha-fr")
    SkillTag.objects.create(name="beta", name_en="beta", name_fr="beta-fr")

    response = client.get(reverse("manage_skill_tags"))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="manual-merge-form"' in content
    assert 'id="manual-source-id"' in content
    assert 'id="manual-target-id"' in content
    assert "Merge any tags" in content


@pytest.mark.django_db
def test_merge_skill_tags_allows_non_suggested_pairs(client, all_apps_user):
    admin_user = all_apps_user()
    client.force_login(admin_user)

    source = SkillTag.objects.create(name="alpha", name_en="alpha", name_fr="alpha")
    target = SkillTag.objects.create(name="omega", name_en="omega", name_fr="omega")

    response = client.post(
        reverse("merge_skill_tags"),
        data={
            "source_id": source.pk,
            "target_id": target.pk,
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("manage_skill_tags")
    assert not SkillTag.objects.filter(pk=source.pk).exists()
    assert SkillTag.objects.filter(pk=target.pk).exists()


@pytest.mark.django_db
def test_manage_skill_tags_suggests_by_embedding_distance(client, all_apps_user):
    admin_user = all_apps_user()
    client.force_login(admin_user)

    SkillTag.objects.create(
        name="alpha-topic",
        name_en="alpha-topic",
        name_fr="alpha-sujet",
        embedding=[0.11, 0.22, 0.33],
    )
    SkillTag.objects.create(
        name="zulu-domain",
        name_en="zulu-domain",
        name_fr="zulu-domaine",
        embedding=[0.11, 0.22, 0.33],
    )

    response = client.get(reverse("manage_skill_tags"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Vector distance" in content
    assert 'Keep "alpha-topic / alpha-sujet"' in content
    assert 'Keep "zulu-domain / zulu-domaine"' in content


@pytest.mark.django_db
def test_manage_skill_tags_backfills_missing_embeddings(client, all_apps_user):
    admin_user = all_apps_user()
    client.force_login(admin_user)

    tag_a = SkillTag.objects.create(
        name="translation",
        name_en="translation",
        name_fr="traduction",
        embedding=None,
    )
    tag_b = SkillTag.objects.create(
        name="translator",
        name_en="translator",
        name_fr="translator",
        embedding=None,
    )

    response = client.get(reverse("manage_skill_tags"))

    assert response.status_code == 200
    tag_a.refresh_from_db()
    tag_b.refresh_from_db()
    assert tag_a.embedding is not None
    assert tag_b.embedding is not None
