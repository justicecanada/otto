from __future__ import annotations

import csv
from datetime import date

from django.http import HttpResponse
from django.utils.dateparse import parse_date

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView
from structlog import get_logger

from otto.api.pagination import OttoLimitOffsetPagination
from otto.api.permissions import OttoApiPermission
from otto.api.serializers import (
    PaginatedUserActivityUserRowSerializer,
    UserActivitySummarySerializer,
)
from otto.services.user_activity_reporting import (
    VALID_ACTIVITY_TYPES,
    VALID_INTERVALS,
    get_user_activity_rows,
    get_user_activity_summary,
)
from otto.utils.api_permissions import API_SCOPE_REPORTING_USER_ACTIVITY_READ

logger = get_logger(__name__)


COMMON_ACTIVITY_PARAMETERS = [
    OpenApiParameter(
        name="start_date",
        type=OpenApiTypes.DATE,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Inclusive start date in YYYY-MM-DD format.",
    ),
    OpenApiParameter(
        name="end_date",
        type=OpenApiTypes.DATE,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Inclusive end date in YYYY-MM-DD format.",
    ),
    OpenApiParameter(
        name="include_inactive",
        type=OpenApiTypes.BOOL,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Include inactive users. Defaults to true.",
    ),
    OpenApiParameter(
        name="activity_type",
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        required=False,
        enum=sorted(VALID_ACTIVITY_TYPES),
        description="Activity metric to summarize or list.",
    ),
]


def _parse_optional_date(value: str | None, field_name: str) -> date | None:
    if not value:
        return None
    parsed = parse_date(value)
    if parsed is None:
        raise ValueError(f"Invalid {field_name}: {value}")
    return parsed


def _parse_bool(value: str | None, *, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"false", "0", "no"}


def _principal_log_context(request) -> dict:
    principal = getattr(request, "api_principal", None) or getattr(
        request._request, "api_principal", None
    )
    return {
        "api_principal_kind": getattr(principal, "kind", None),
        "user_id": getattr(getattr(principal, "user", None), "id", None),
        "api_client_id": getattr(getattr(principal, "client", None), "id", None),
        "api_client_name": getattr(getattr(principal, "client", None), "name", None),
    }


def _build_csv_response(rows: list[dict]) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="user_activity.csv"'

    writer = csv.writer(response)
    headers = [
        "upn",
        "email",
        "oid",
        "date_joined",
        "last_login",
        "is_active",
        "first_activity_date",
        "last_activity_date",
        "active_days",
        "activity_events",
        "chat_messages",
        "files_created",
        "text_extractor_requests",
        "laws_queries",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "embedding_tokens",
        "total_tokens",
        "sign_in_at_least_once",
        "signed_in_in_period",
        "selected_activity_total",
        "used_in_period",
    ]
    writer.writerow(headers)
    for row in rows:
        writer.writerow([row.get(header) for header in headers])
    return response


class OttoApiView(APIView):
    permission_classes = [OttoApiPermission]
    human_permission = "otto.manage_users"
    machine_scopes = (API_SCOPE_REPORTING_USER_ACTIVITY_READ,)


class UserActivitySummaryView(OttoApiView):
    @extend_schema(
        tags=["Reporting"],
        summary="Get user activity summary",
        description="Returns aggregate user activity totals and time buckets for the selected activity type.",
        parameters=[
            *COMMON_ACTIVITY_PARAMETERS,
            OpenApiParameter(
                name="interval",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=sorted(VALID_INTERVALS),
                description="Bucket interval for the summary. Defaults to month.",
            ),
        ],
        responses=UserActivitySummarySerializer,
    )
    def get(self, request):
        try:
            start_date = _parse_optional_date(
                request.query_params.get("start_date"), "start_date"
            )
            end_date = _parse_optional_date(
                request.query_params.get("end_date"), "end_date"
            )
            include_inactive = _parse_bool(
                request.query_params.get("include_inactive"), default=True
            )
            activity_type = request.query_params.get("activity_type", "any_usage")
            interval = request.query_params.get("interval", "month")
            summary = get_user_activity_summary(
                start_date=start_date,
                end_date=end_date,
                include_inactive=include_inactive,
                activity_type=activity_type,
                interval=interval,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)

        logger.info(
            "user_activity_summary_requested",
            activity_type=activity_type,
            interval=interval,
            start_date=summary["start_date"],
            end_date=summary["end_date"],
            include_inactive=include_inactive,
            **_principal_log_context(request),
        )
        return Response(summary)


class UserActivityUsersView(OttoApiView):
    pagination_class = OttoLimitOffsetPagination

    @extend_schema(
        tags=["Reporting"],
        summary="List per-user activity rows",
        description="Returns paginated per-user activity rows as JSON, or a CSV export when `format=csv`.",
        parameters=[
            *COMMON_ACTIVITY_PARAMETERS,
            OpenApiParameter(
                name="format",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=["json", "csv"],
                description="Response format. Defaults to json.",
            ),
            OpenApiParameter(
                name="limit",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Page size for JSON responses.",
            ),
            OpenApiParameter(
                name="offset",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Pagination offset for JSON responses.",
            ),
        ],
        responses={
            (200, "application/json"): PaginatedUserActivityUserRowSerializer,
            (200, "text/csv"): OpenApiTypes.BINARY,
        },
    )
    def get(self, request):
        try:
            start_date = _parse_optional_date(
                request.query_params.get("start_date"), "start_date"
            )
            end_date = _parse_optional_date(
                request.query_params.get("end_date"), "end_date"
            )
            include_inactive = _parse_bool(
                request.query_params.get("include_inactive"), default=True
            )
            activity_type = request.query_params.get("activity_type", "any_usage")
            response_format = request.query_params.get("format", "json").strip().lower()
            rows = get_user_activity_rows(
                start_date=start_date,
                end_date=end_date,
                include_inactive=include_inactive,
                activity_type=activity_type,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)

        logger.info(
            "user_activity_users_requested",
            activity_type=activity_type,
            start_date=start_date.isoformat() if start_date else None,
            end_date=end_date.isoformat() if end_date else None,
            include_inactive=include_inactive,
            response_format=response_format,
            row_count=len(rows),
            **_principal_log_context(request),
        )

        if response_format == "csv":
            return _build_csv_response(rows)
        if response_format != "json":
            return Response(
                {"detail": f"Unsupported format: {response_format}"}, status=400
            )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(rows, request, view=self)
        if page is None:
            return Response({"count": len(rows), "results": rows})
        return paginator.get_paginated_response(page)
