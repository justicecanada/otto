import datetime

import pytest

from otto.models import Cost, CostType, User
from otto.services.user_activity_reporting import (
    get_user_activity_rows,
    get_user_activity_summary,
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
def test_get_user_activity_rows_aggregates_metrics(cost_type_factory):
    chat_output = cost_type_factory("chat-output", name="GPT output tokens")
    chat_input = cost_type_factory("chat-input", name="GPT input tokens")
    cached_input = cost_type_factory("gpt-5-in-cached", name="GPT cached input")
    law_type = cost_type_factory("laws-query", name="Laws query")
    text_extractor_type = cost_type_factory("text-extractor", name="Text extractor")

    user = User.objects.create_user(
        upn="person.one@example.com",
        email="person.one@example.com",
        oid="person-one",
    )

    first_day = datetime.date(2025, 1, 5)
    second_day = datetime.date(2025, 1, 7)

    output_cost = Cost.objects.create(
        cost_type=chat_output,
        count=25,
        usd_cost=1,
        feature="chat",
        user=user,
    )
    input_cost = Cost.objects.create(
        cost_type=chat_input,
        count=120,
        usd_cost=1,
        feature="chat",
        user=user,
    )
    cached_cost = Cost.objects.create(
        cost_type=cached_input,
        count=30,
        usd_cost=1,
        feature="chat_next",
        user=user,
    )
    law_cost = Cost.objects.create(
        cost_type=law_type,
        count=1,
        usd_cost=1,
        feature="laws_query",
        user=user,
    )
    text_cost_1 = Cost.objects.create(
        cost_type=text_extractor_type,
        count=1,
        usd_cost=1,
        feature="text_extractor",
        user=user,
        request_id="req-1",
    )
    text_cost_2 = Cost.objects.create(
        cost_type=text_extractor_type,
        count=1,
        usd_cost=1,
        feature="text_extractor",
        user=user,
        request_id="req-1",
    )
    text_cost_3 = Cost.objects.create(
        cost_type=text_extractor_type,
        count=1,
        usd_cost=1,
        feature="text_extractor",
        user=user,
        request_id="req-2",
    )

    for cost in [output_cost, input_cost, cached_cost]:
        cost.date_incurred = first_day
        cost.save(update_fields=["date_incurred"])
    for cost in [law_cost, text_cost_1, text_cost_2, text_cost_3]:
        cost.date_incurred = second_day
        cost.save(update_fields=["date_incurred"])

    rows = get_user_activity_rows(
        start_date=datetime.date(2025, 1, 1),
        end_date=datetime.date(2025, 1, 31),
        activity_type="any_usage",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["upn"] == "person.one@example.com"
    assert row["first_activity_date"] == "2025-01-05"
    assert row["last_activity_date"] == "2025-01-07"
    assert row["active_days"] == 2
    assert row["activity_events"] == 7
    assert row["chat_messages"] == 1
    assert row["input_tokens"] == 120
    assert row["cached_input_tokens"] == 30
    assert row["output_tokens"] == 25
    assert row["laws_queries"] == 1
    assert row["text_extractor_requests"] == 2
    assert row["total_tokens"] == 175
    assert row["selected_activity_total"] == 7
    assert row["used_in_period"] is True


@pytest.mark.django_db
def test_get_user_activity_summary_buckets_monthly_tokens(cost_type_factory):
    chat_input = cost_type_factory("chat-input", name="GPT input tokens")
    user_one = User.objects.create_user(
        upn="user.one@example.com",
        email="user.one@example.com",
        oid="user-one",
    )
    user_two = User.objects.create_user(
        upn="user.two@example.com",
        email="user.two@example.com",
        oid="user-two",
    )

    january = Cost.objects.create(
        cost_type=chat_input,
        count=100,
        usd_cost=1,
        feature="chat",
        user=user_one,
    )
    february = Cost.objects.create(
        cost_type=chat_input,
        count=50,
        usd_cost=1,
        feature="chat",
        user=user_two,
    )
    january.date_incurred = datetime.date(2025, 1, 15)
    january.save(update_fields=["date_incurred"])
    february.date_incurred = datetime.date(2025, 2, 2)
    february.save(update_fields=["date_incurred"])

    summary = get_user_activity_summary(
        start_date=datetime.date(2025, 1, 1),
        end_date=datetime.date(2025, 2, 28),
        activity_type="input_tokens",
        interval="month",
    )

    assert summary["total_users"] == 2
    assert summary["total_activity"] == 150
    assert summary["buckets"] == [
        {"label": "2025-01", "distinct_users": 1, "activity_total": 100},
        {"label": "2025-02", "distinct_users": 1, "activity_total": 50},
    ]
