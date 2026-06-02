import datetime

from django.utils import timezone

import pytest

from otto.models import Cost, CostGroup, CostType, User
from otto.utils.usage_dashboard_utils import (
    aggregate_counts,
    calculate_aggregated_dashboard_number,
    filter_group_count_types,
)


@pytest.fixture
def cost_type_factory():
    def _factory(short_name, name=None):
        cost_type, _created = CostType.objects.get_or_create(
            short_name=short_name,
            defaults={
                "name": name or short_name,
                "description": f"Description for {short_name}",
                "unit_name": "unit",
                "unit_cost": 1,
                "unit_quantity": 1,
            },
        )
        return cost_type

    return _factory


@pytest.mark.django_db
def test_filter_group_count_types_includes_translate_tokens(cost_type_factory):
    output_type = cost_type_factory("chat-output", name="GPT output tokens")
    translate_type = cost_type_factory("translate-text", name="Azure translate text")
    ignored_type = cost_type_factory("chat-input", name="GPT input tokens")

    Cost.objects.create(
        cost_type=output_type,
        count=10,
        usd_cost=1,
        feature="chat",
    )
    Cost.objects.create(
        cost_type=translate_type,
        count=5,
        usd_cost=1,
        feature="translate",
    )
    Cost.objects.create(
        cost_type=ignored_type,
        count=5,
        usd_cost=1,
        feature="chat",
    )

    qs = filter_group_count_types(Cost.objects.all(), "chat_messages")
    returned_short_names = set(qs.values_list("cost_type__short_name", flat=True))

    assert returned_short_names == {"chat-output", "translate-text"}


@pytest.mark.django_db
def test_filter_group_count_types_files_created_combines_sources(cost_type_factory):
    translate_file = cost_type_factory("translate-file", name="Azure translate file")
    text_extractor_type = cost_type_factory(
        "text-extractor", name="Text extractor cost"
    )

    Cost.objects.create(
        cost_type=translate_file,
        count=1,
        usd_cost=1,
        feature="translate",
    )
    Cost.objects.create(
        cost_type=text_extractor_type,
        count=1,
        usd_cost=1,
        feature="text_extractor",
    )

    qs = filter_group_count_types(Cost.objects.all(), "files_created")
    features = set(qs.values_list("feature", flat=True))

    assert features == {"translate", "text_extractor"}


@pytest.mark.django_db
def test_filter_group_count_types_embedding_tokens_includes_librarian(
    cost_type_factory,
):
    chat_embed = cost_type_factory("chat-embed", name="Chat embedding tokens")
    librarian_embed = cost_type_factory(
        "librarian-embed", name="Librarian embedding tokens"
    )

    Cost.objects.create(
        cost_type=chat_embed,
        count=2,
        usd_cost=1,
        feature="chat",
    )
    Cost.objects.create(
        cost_type=librarian_embed,
        count=3,
        usd_cost=1,
        feature="librarian",
    )

    qs = filter_group_count_types(Cost.objects.all(), "embedding_tokens")
    features = set(qs.values_list("feature", flat=True))

    assert features == {"chat", "librarian"}


@pytest.mark.django_db
def test_filter_group_count_types_files_created_translate_only(cost_type_factory):
    translate_file = cost_type_factory("translate-file", name="Azure translate file")
    text_extractor_type = cost_type_factory(
        "text-extractor", name="Text extractor cost"
    )
    Cost.objects.create(
        cost_type=translate_file,
        count=1,
        usd_cost=1,
        feature="translate",
    )
    Cost.objects.create(
        cost_type=text_extractor_type,
        count=1,
        usd_cost=1,
        feature="text_extractor",
    )

    qs = filter_group_count_types(
        Cost.objects.all(), "files_created", chat_type="translate"
    )
    features = set(qs.values_list("feature", flat=True))

    assert features == {"translate"}


@pytest.mark.django_db
def test_filter_group_count_types_laws_query_filter(cost_type_factory):
    law_type = cost_type_factory("laws-query", name="Laws query")
    other_type = cost_type_factory("chat-output", name="GPT output tokens")

    Cost.objects.create(
        cost_type=law_type,
        count=1,
        usd_cost=1,
        feature="laws_query",
    )
    Cost.objects.create(
        cost_type=other_type,
        count=1,
        usd_cost=1,
        feature="chat",
    )

    qs = filter_group_count_types(Cost.objects.all(), "laws_query")
    features = set(qs.values_list("feature", flat=True))

    assert features == {"laws_query"}


@pytest.mark.django_db
def test_aggregate_counts_fills_missing_dates(cost_type_factory):
    token_type = cost_type_factory("chat-input", name="GPT input tokens")

    today = timezone.now().date()
    two_days_ago = today - datetime.timedelta(days=2)
    yesterday = today - datetime.timedelta(days=1)

    first = Cost.objects.create(
        cost_type=token_type,
        count=10,
        usd_cost=1,
        feature="chat",
    )
    first.date_incurred = two_days_ago
    first.save(update_fields=["date_incurred"])

    second = Cost.objects.create(
        cost_type=token_type,
        count=5,
        usd_cost=1,
        feature="chat",
    )
    second.date_incurred = today
    second.save(update_fields=["date_incurred"])

    counts = aggregate_counts(
        Cost.objects.filter(cost_type=token_type).order_by("date_incurred"),
        x_axis="day",
        end_date=today,
        count_type="input_tokens",
    )

    normalized = [
        {
            "day": entry.get("day") or entry.get("date_incurred"),
            "total_count": entry["total_count"],
        }
        for entry in counts
    ]

    assert normalized == [
        {"day": two_days_ago, "total_count": 10},
        {"day": yesterday, "total_count": 0},
        {"day": today, "total_count": 5},
    ]


@pytest.mark.django_db
def test_aggregate_counts_cost_group_includes_personal(cost_type_factory):
    token_type = cost_type_factory("chat-input", name="GPT input tokens")
    group = CostGroup.objects.create(cost_group_id="grp-1", name="Group 1")

    Cost.objects.create(
        cost_type=token_type,
        count=7,
        usd_cost=1,
        feature="chat",
        cost_group=group,
    )
    Cost.objects.create(
        cost_type=token_type,
        count=5,
        usd_cost=1,
        feature="chat",
        cost_group=None,
    )

    counts = aggregate_counts(
        Cost.objects.filter(cost_type=token_type),
        x_axis="cost_group",
        count_type="input_tokens",
    )

    normalized = [
        {
            "cost_group": entry.get("cost_group") or entry.get("cost_group_display"),
            "total_count": entry["total_count"],
        }
        for entry in counts
    ]

    sorted_counts = sorted(normalized, key=lambda c: c["cost_group"])

    assert sorted_counts == [
        {"cost_group": "Group 1", "total_count": 7},
        {"cost_group": "No cost group (personal costs)", "total_count": 5},
    ]


@pytest.mark.django_db
def test_aggregate_counts_user_axis_counts_rows(cost_type_factory):
    output_type = cost_type_factory("chat-output", name="GPT output tokens")
    user_one = User.objects.create(
        upn="user1@example.com",
        email="user1@example.com",
        first_name="User",
        last_name="One",
    )
    user_two = User.objects.create(
        upn="user2@example.com",
        email="user2@example.com",
        first_name="User",
        last_name="Two",
    )

    Cost.objects.create(
        cost_type=output_type,
        count=1,
        usd_cost=1,
        feature="chat",
        user=user_one,
    )
    Cost.objects.create(
        cost_type=output_type,
        count=1,
        usd_cost=1,
        feature="chat",
        user=user_two,
    )
    Cost.objects.create(
        cost_type=output_type,
        count=1,
        usd_cost=1,
        feature="chat",
        user=user_two,
    )

    counts = aggregate_counts(
        filter_group_count_types(Cost.objects.all(), "chat_messages"),
        x_axis="user",
        count_type="chat_messages",
    )

    normalized = [
        {
            "user": entry.get("user") or entry.get("user__upn"),
            "total_count": entry["total_count"],
        }
        for entry in counts
    ]

    sorted_counts = sorted(normalized, key=lambda c: c["user"])

    assert sorted_counts == [
        {"user": "user1@example.com", "total_count": 1},
        {"user": "user2@example.com", "total_count": 2},
    ]


@pytest.mark.django_db
def test_aggregate_counts_weekly_sums(cost_type_factory):
    token_type = cost_type_factory("chat-input", name="GPT input tokens")

    # Use a fixed date in the middle of a week to avoid week boundary issues
    # (e.g., running tests near year-end when +2 days crosses into a new week)
    base_date = datetime.date(2025, 6, 10)  # Tuesday, June 10, 2025
    for offset, count in [(0, 3), (1, 5), (2, 7)]:
        entry = Cost.objects.create(
            cost_type=token_type,
            count=count,
            usd_cost=1,
            feature="chat",
        )
        entry.date_incurred = base_date + datetime.timedelta(days=offset)
        entry.save(update_fields=["date_incurred"])

    counts = aggregate_counts(
        Cost.objects.filter(cost_type=token_type).order_by("date_incurred"),
        x_axis="week",
        end_date=base_date + datetime.timedelta(days=2),
        count_type="input_tokens",
    )

    # Use isocalendar() to match the implementation in aggregate_counts
    expected_week = f"{base_date.isocalendar()[0]}-{base_date.isocalendar()[1]:02d}"

    assert counts == [{"week": expected_week, "total_count": 15}]


@pytest.mark.django_db
def test_calculate_aggregated_dashboard_number_handles_tokens_and_rows(
    cost_type_factory,
):
    token_type = cost_type_factory("chat-input", name="GPT input tokens")
    message_type = cost_type_factory("chat-output", name="GPT output tokens")

    Cost.objects.create(cost_type=token_type, count=20, usd_cost=2, feature="chat")
    Cost.objects.create(cost_type=token_type, count=5, usd_cost=1, feature="chat")
    Cost.objects.create(cost_type=message_type, count=1, usd_cost=1, feature="chat")

    token_total = calculate_aggregated_dashboard_number(
        Cost.objects.filter(cost_type=token_type),
        count_type="input_tokens",
    )
    message_total = calculate_aggregated_dashboard_number(
        Cost.objects.filter(cost_type=message_type),
        count_type="chat_messages",
    )

    assert token_total == 25
    assert message_total == 1


@pytest.mark.django_db
def test_calculate_aggregated_dashboard_number_returns_zero_when_empty(
    cost_type_factory,
):
    token_type = cost_type_factory("chat-input", name="GPT input tokens")

    total = calculate_aggregated_dashboard_number(
        Cost.objects.filter(cost_type=token_type),
        count_type="input_tokens",
    )

    assert total == 0
