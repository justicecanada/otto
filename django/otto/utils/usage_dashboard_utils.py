from datetime import timedelta

from django.db import models
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from otto.models import CHAT_FEATURES


def filter_group_count_types(counts, count_type, chat_type=None):
    chat_features = []
    # Determine which chat features to include
    if chat_type == "all" or chat_type is None:
        if count_type == "embedding_tokens":
            chat_features = CHAT_FEATURES + ["librarian"]
        else:
            chat_features = CHAT_FEATURES
    else:
        chat_features = [chat_type]

    def filter_chat_features(counts, features, cost_type_name=None):
        qs = counts.filter(feature__in=features)
        if cost_type_name:
            qs = qs.filter(cost_type__name__icontains=cost_type_name)
        return qs

    if count_type == "chat_messages":
        # Filter to cost types that occur once per response to avoid over-counting
        # For chat/qa/summarize/translate: output tokens
        # For Azure translate: translation cost types (translate-text, translate-file, translate-custom)

        # GPT-based responses (including GPT translation)
        chat_qs = filter_chat_features(counts, chat_features, "output")

        # Azure translation (only if translate is in features)
        azure_translate_qs = None
        if "translate" in chat_features:
            azure_translate_qs = counts.filter(
                feature="translate", cost_type__short_name__startswith="translate-"
            )

        if azure_translate_qs is not None:
            counts = chat_qs | azure_translate_qs
        else:
            counts = chat_qs
    if count_type == "input_tokens":
        counts = filter_chat_features(counts, chat_features, "input")
    if count_type == "output_tokens":
        counts = filter_chat_features(counts, chat_features, "output")
    if count_type == "embedding_tokens":
        # handle librarian separately because its cost_type name contains "embedding"
        librarian_qs = None
        if "librarian" in chat_features:
            librarian_qs = counts.filter(
                feature="librarian", cost_type__name__icontains="embedding"
            )
            chat_features = [f for f in chat_features if f != "librarian"]

        chat_qs = (
            filter_chat_features(counts, chat_features, "embedding")
            if chat_features
            else None
        )

        if librarian_qs and chat_qs:
            counts = chat_qs | librarian_qs
        elif librarian_qs:
            counts = librarian_qs
        elif chat_qs:
            counts = chat_qs
        else:
            counts = counts.none()
    if count_type == "files_created":
        # Files created: translate-file cost type or text_extractor feature
        if chat_type == "translate":
            counts = counts.filter(cost_type__short_name="translate-file")
        elif chat_type == "text_extractor":
            counts = counts.filter(feature="text_extractor")
        else:
            # "all" - combine both sources
            translate_qs = counts.filter(cost_type__short_name="translate-file")
            text_extractor_qs = counts.filter(feature="text_extractor")
            counts = translate_qs | text_extractor_qs
    if count_type == "laws_query":
        counts = counts.filter(feature="laws_query")
    return counts


def aggregate_counts(counts, x_axis="day", end_date=None, count_type=None):
    # Aggregate the counts by the selected x-axis
    def aggregate_by_field(field_name, label):
        if count_type in ["input_tokens", "output_tokens", "embedding_tokens"]:
            qs = counts.values(field_name).annotate(total_count=models.Sum("count"))
        elif count_type in ["chat_messages", "files_created", "laws_query"]:
            # Count rows - already filtered to appropriate cost types
            qs = counts.values(field_name).annotate(total_count=models.Count("id"))
        else:
            qs = counts.values(field_name).annotate(
                total_count=models.Count(field_name)
            )
        return [{**c, label: c.pop(field_name)} for c in qs]

    if x_axis == "cost_group":
        counts = counts.annotate(
            cost_group_display=Coalesce(
                "cost_group__name", Value(str(_("No cost group (personal costs)")))
            )
        )
        if count_type in ["input_tokens", "output_tokens", "embedding_tokens"]:
            counts = counts.values("cost_group_display").annotate(
                total_count=models.Sum("count")
            )
        elif count_type in ["chat_messages", "files_created", "laws_query"]:
            # Count rows - already filtered to appropriate cost types
            counts = counts.values("cost_group_display").annotate(
                total_count=models.Count("id")
            )
        else:
            counts = counts.values("cost_group_display").annotate(
                total_count=models.Count("cost_group_display", distinct=True)
            )
        counts = [{**c, "cost_group": c.pop("cost_group_display")} for c in counts]
    elif x_axis == "user":
        counts = aggregate_by_field("user__upn", "user")
    else:
        # Special handling for dates
        if (
            count_type == "input_tokens"
            or count_type == "output_tokens"
            or count_type == "embedding_tokens"
        ):
            counts = counts.values("date_incurred").annotate(
                total_count=models.Sum("count")
            )
        elif count_type in ["chat_messages", "files_created", "laws_query"]:
            # Count rows - already filtered to appropriate cost types
            counts = counts.values("date_incurred").annotate(
                total_count=models.Count("id")
            )
        else:
            counts = counts.values("date_incurred").annotate(
                total_count=models.Count("feature", distinct=True)
            )
        counts = [{**c, "day": c.pop("date_incurred")} for c in counts]
        # Fill missing dates (if any) with zero counts, up until today's date
        if counts:
            start_date = counts[0]["day"]
        else:
            start_date = timezone.now().date()
        if not end_date:
            end_date = timezone.now().date()
        date_range = [
            start_date + timedelta(days=x)
            for x in range((end_date - start_date).days + 1)
        ]
        counts_dict = {c["day"]: c for c in counts}
        counts = [
            counts_dict.get(date, {"day": date, "total_count": 0})
            for date in date_range
        ]
        if x_axis == "week":
            counts = [
                {
                    # Use ISO year and ISO week number for correct week labeling
                    "week": f"{c['day'].isocalendar()[0]}-{c['day'].isocalendar()[1]:02d}",
                    "total_count": c["total_count"],
                }
                for c in counts
            ]
        elif x_axis == "month":
            counts = [
                {
                    "month": c["day"].strftime("%Y-%m"),
                    "total_count": c["total_count"],
                }
                for c in counts
            ]
        if x_axis in ["week", "month"]:
            # Sum the costs for each week or month
            counts = [
                {
                    f"{x_axis}": week_or_month,
                    "total_count": sum(
                        c["total_count"] for c in counts if c[x_axis] == week_or_month
                    ),
                }
                for week_or_month in set(c[x_axis] for c in counts)
            ]
        # Sort by x-axis label
        counts = sorted(counts, key=lambda c: c[x_axis])
    return counts


def calculate_aggregated_dashboard_number(raw_costs, count_type):
    if count_type in ["input_tokens", "output_tokens", "embedding_tokens"]:
        aggregated_number = raw_costs.aggregate(total_count=models.Sum("count"))[
            "total_count"
        ]
    else:
        # For chat_messages, files_created, laws_query - count the filtered Cost records
        aggregated_number = raw_costs.count()
    return aggregated_number or 0
