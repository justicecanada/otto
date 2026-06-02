from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from django.db.models import QuerySet, Sum

from otto.models import CHAT_FEATURES, Cost, User
from otto.utils.usage_dashboard_utils import filter_group_count_types

USER_ACTIVITY_FEATURES = tuple(
    CHAT_FEATURES + ["librarian", "text_extractor", "laws_query"]
)
VALID_ACTIVITY_TYPES = {
    "any_usage",
    "sign_in",
    "chat_messages",
    "files_created",
    "text_extractor_requests",
    "laws_query",
    "input_tokens",
    "output_tokens",
    "embedding_tokens",
}
VALID_INTERVALS = {"day", "week", "month", "none"}
TOKEN_ACTIVITY_TYPES = {"input_tokens", "output_tokens", "embedding_tokens"}
ROW_COUNT_ACTIVITY_TYPES = {"chat_messages", "files_created", "laws_query"}


def _validate_activity_type(activity_type: str) -> str:
    if activity_type not in VALID_ACTIVITY_TYPES:
        raise ValueError(f"Unsupported activity_type: {activity_type}")
    return activity_type


def _validate_interval(interval: str) -> str:
    if interval not in VALID_INTERVALS:
        raise ValueError(f"Unsupported interval: {interval}")
    return interval


def _bucket_label(value: date, interval: str) -> str:
    if interval == "day":
        return value.isoformat()
    if interval == "week":
        year, week_number, _weekday = value.isocalendar()
        return f"{year}-{week_number:02d}"
    if interval == "month":
        return value.strftime("%Y-%m")
    raise ValueError(f"Unsupported interval: {interval}")


def _filtered_users(*, include_inactive: bool) -> QuerySet[User]:
    queryset = User.objects.all().order_by("upn")
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset


def _base_costs(
    *, start_date: date | None, end_date: date | None, include_inactive: bool
) -> QuerySet[Cost]:
    queryset = Cost.objects.filter(
        user__isnull=False,
        feature__in=USER_ACTIVITY_FEATURES,
    ).select_related("user", "cost_type")
    if start_date:
        queryset = queryset.filter(date_incurred__gte=start_date)
    if end_date:
        queryset = queryset.filter(date_incurred__lte=end_date)
    if not include_inactive:
        queryset = queryset.filter(user__is_active=True)
    return queryset


def _activity_costs(
    activity_type: str,
    *,
    start_date: date | None,
    end_date: date | None,
    include_inactive: bool,
) -> QuerySet[Cost]:
    activity_type = _validate_activity_type(activity_type)
    queryset = _base_costs(
        start_date=start_date,
        end_date=end_date,
        include_inactive=include_inactive,
    )
    if activity_type == "any_usage":
        return queryset
    if activity_type == "text_extractor_requests":
        return queryset.filter(feature="text_extractor")
    if activity_type == "sign_in":
        return queryset.none()
    queryset = filter_group_count_types(queryset, activity_type)
    if activity_type == "input_tokens":
        queryset = queryset.exclude(cost_type__short_name__endswith="-in-cached")
    return queryset


def _initial_row(user: User) -> dict[str, Any]:
    return {
        "upn": user.upn,
        "email": user.email,
        "oid": user.oid,
        "date_joined": user.date_joined.date().isoformat()
        if user.date_joined
        else None,
        "last_login": user.last_login.isoformat() if user.last_login else None,
        "is_active": user.is_active,
        "first_activity_date": None,
        "last_activity_date": None,
        "active_days": 0,
        "activity_events": 0,
        "chat_messages": 0,
        "files_created": 0,
        "text_extractor_requests": 0,
        "laws_queries": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "embedding_tokens": 0,
        "total_tokens": 0,
        "sign_in_at_least_once": user.last_login is not None,
        "signed_in_in_period": False,
        "selected_activity_total": 0,
        "used_in_period": False,
        "_active_dates": set(),
        "_text_extractor_request_keys": set(),
    }


def get_user_activity_rows(
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    include_inactive: bool = True,
    activity_type: str = "any_usage",
) -> list[dict[str, Any]]:
    activity_type = _validate_activity_type(activity_type)

    users = list(_filtered_users(include_inactive=include_inactive))
    rows_by_user_id = {user.id: _initial_row(user) for user in users}

    if not rows_by_user_id:
        return []

    base_costs = _base_costs(
        start_date=start_date,
        end_date=end_date,
        include_inactive=include_inactive,
    )

    chat_message_ids = set(
        filter_group_count_types(base_costs, "chat_messages").values_list(
            "id", flat=True
        )
    )
    files_created_ids = set(
        filter_group_count_types(base_costs, "files_created").values_list(
            "id", flat=True
        )
    )
    laws_query_ids = set(
        filter_group_count_types(base_costs, "laws_query").values_list("id", flat=True)
    )

    for cost in base_costs.only(
        "id",
        "user_id",
        "date_incurred",
        "count",
        "feature",
        "request_id",
        "cost_type__name",
        "cost_type__short_name",
    ):
        row = rows_by_user_id.get(cost.user_id)
        if row is None:
            continue

        row["activity_events"] += 1
        row["_active_dates"].add(cost.date_incurred)

        if (
            row["first_activity_date"] is None
            or cost.date_incurred.isoformat() < row["first_activity_date"]
        ):
            row["first_activity_date"] = cost.date_incurred.isoformat()
        if (
            row["last_activity_date"] is None
            or cost.date_incurred.isoformat() > row["last_activity_date"]
        ):
            row["last_activity_date"] = cost.date_incurred.isoformat()

        if cost.id in chat_message_ids:
            row["chat_messages"] += 1
        if cost.id in files_created_ids:
            row["files_created"] += 1
        if cost.id in laws_query_ids:
            row["laws_queries"] += 1

        cost_type_name = (getattr(cost.cost_type, "name", "") or "").lower()
        short_name = getattr(cost.cost_type, "short_name", "") or ""

        if short_name.endswith("-in-cached"):
            row["cached_input_tokens"] += cost.count
        elif cost.feature in CHAT_FEATURES and "input" in cost_type_name:
            row["input_tokens"] += cost.count

        if cost.feature in CHAT_FEATURES and "output" in cost_type_name:
            row["output_tokens"] += cost.count

        if (
            cost.feature in CHAT_FEATURES or cost.feature == "librarian"
        ) and "embedding" in cost_type_name:
            row["embedding_tokens"] += cost.count

        if cost.feature == "text_extractor":
            request_key = cost.request_id or f"cost-{cost.id}"
            row["_text_extractor_request_keys"].add(request_key)

    for user in users:
        row = rows_by_user_id[user.id]
        row["active_days"] = len(row.pop("_active_dates"))
        row["text_extractor_requests"] = len(row.pop("_text_extractor_request_keys"))
        row["total_tokens"] = (
            row["input_tokens"]
            + row["cached_input_tokens"]
            + row["output_tokens"]
            + row["embedding_tokens"]
        )

        if user.last_login is not None:
            last_login_date = user.last_login.date()
            row["signed_in_in_period"] = (
                start_date is None or last_login_date >= start_date
            ) and (end_date is None or last_login_date <= end_date)

        if activity_type == "any_usage":
            row["selected_activity_total"] = row["activity_events"]
        elif activity_type == "sign_in":
            row["selected_activity_total"] = 1 if row["signed_in_in_period"] else 0
        elif activity_type == "chat_messages":
            row["selected_activity_total"] = row["chat_messages"]
        elif activity_type == "files_created":
            row["selected_activity_total"] = row["files_created"]
        elif activity_type == "text_extractor_requests":
            row["selected_activity_total"] = row["text_extractor_requests"]
        elif activity_type == "laws_query":
            row["selected_activity_total"] = row["laws_queries"]
        elif activity_type == "input_tokens":
            row["selected_activity_total"] = row["input_tokens"]
        elif activity_type == "output_tokens":
            row["selected_activity_total"] = row["output_tokens"]
        elif activity_type == "embedding_tokens":
            row["selected_activity_total"] = row["embedding_tokens"]

        row["used_in_period"] = row["selected_activity_total"] > 0

    return [row for row in rows_by_user_id.values() if row["used_in_period"]]


def get_user_activity_summary(
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    include_inactive: bool = True,
    activity_type: str = "any_usage",
    interval: str = "month",
) -> dict[str, Any]:
    activity_type = _validate_activity_type(activity_type)
    interval = _validate_interval(interval)

    summary: dict[str, Any] = {
        "activity_type": activity_type,
        "interval": interval,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "include_inactive": include_inactive,
        "total_users": 0,
        "total_activity": 0,
        "buckets": [],
    }

    if interval == "none":
        if activity_type == "sign_in":
            users = _filtered_users(include_inactive=include_inactive)
            if start_date:
                users = users.filter(last_login__date__gte=start_date)
            if end_date:
                users = users.filter(last_login__date__lte=end_date)
            summary["total_users"] = users.exclude(last_login__isnull=True).count()
            summary["total_activity"] = summary["total_users"]
            return summary

        costs = _activity_costs(
            activity_type,
            start_date=start_date,
            end_date=end_date,
            include_inactive=include_inactive,
        )
        if activity_type == "any_usage":
            costs = costs.filter(feature__in=USER_ACTIVITY_FEATURES)

        summary["total_users"] = costs.values("user_id").distinct().count()
        if activity_type in TOKEN_ACTIVITY_TYPES:
            summary["total_activity"] = (
                costs.aggregate(total=Sum("count"))["total"] or 0
            )
        elif activity_type == "text_extractor_requests":
            summary["total_activity"] = len(
                {
                    (cost.user_id, cost.request_id or f"cost-{cost.id}")
                    for cost in costs.only("id", "user_id", "request_id")
                }
            )
        else:
            summary["total_activity"] = costs.count()
        return summary

    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"label": "", "activity_total": 0, "_users": set(), "_requests": set()}
    )
    total_user_ids: set[int] = set()

    if activity_type == "sign_in":
        users = _filtered_users(include_inactive=include_inactive).exclude(
            last_login__isnull=True
        )
        if start_date:
            users = users.filter(last_login__date__gte=start_date)
        if end_date:
            users = users.filter(last_login__date__lte=end_date)

        for user in users.only("id", "last_login"):
            bucket_date = user.last_login.date()
            label = _bucket_label(bucket_date, interval)
            bucket = buckets[label]
            bucket["label"] = label
            bucket["activity_total"] += 1
            bucket["_users"].add(user.id)
            total_user_ids.add(user.id)
        summary["total_activity"] = len(total_user_ids)
    else:
        costs = _activity_costs(
            activity_type,
            start_date=start_date,
            end_date=end_date,
            include_inactive=include_inactive,
        )
        if activity_type == "any_usage":
            costs = costs.filter(feature__in=USER_ACTIVITY_FEATURES)

        total_activity = 0
        if activity_type in TOKEN_ACTIVITY_TYPES:
            total_activity = costs.aggregate(total=Sum("count"))["total"] or 0
        elif activity_type == "text_extractor_requests":
            distinct_request_keys = set()
            for cost in costs:
                distinct_request_keys.add(
                    (cost.user_id, cost.request_id or f"cost-{cost.id}")
                )
            total_activity = len(distinct_request_keys)
        else:
            total_activity = costs.count()
        summary["total_activity"] = total_activity

        for cost in costs:
            label = _bucket_label(cost.date_incurred, interval)
            bucket = buckets[label]
            bucket["label"] = label
            bucket["_users"].add(cost.user_id)
            total_user_ids.add(cost.user_id)
            if activity_type in TOKEN_ACTIVITY_TYPES:
                bucket["activity_total"] += cost.count
            elif activity_type == "text_extractor_requests":
                bucket["_requests"].add(
                    (cost.user_id, cost.request_id or f"cost-{cost.id}")
                )
            else:
                bucket["activity_total"] += 1

    bucket_rows = []
    for label in sorted(buckets):
        bucket = buckets[label]
        activity_total = bucket["activity_total"]
        if activity_type == "text_extractor_requests":
            activity_total = len(bucket["_requests"])
        bucket_rows.append(
            {
                "label": bucket["label"],
                "distinct_users": len(bucket["_users"]),
                "activity_total": activity_total,
            }
        )

    summary["total_users"] = len(total_user_ids)
    summary["buckets"] = bucket_rows
    return summary
