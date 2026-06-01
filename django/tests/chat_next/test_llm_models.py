from django.utils.translation import gettext as _
from django.utils.translation import override

from chat_next._llm.models import (
    MODELS_BY_ID,
    get_compaction_threshold_percentage,
    get_compaction_threshold_tokens,
    get_context_usage_display,
    get_grouped_chat_model_choices,
    get_supported_reasoning_efforts,
    normalize_reasoning_effort,
)


def _choice_metadata_by_model_id():
    grouped = get_grouped_chat_model_choices()
    return {
        model_id: metadata
        for _group_name, choices in grouped
        for model_id, metadata in choices
    }


def test_chat_next_gpt_5_catalog_matches_live_reasoning_support():
    expected_efforts = {
        "gpt-5": ("minimal", "low", "medium", "high"),
        "gpt-5.1": ("none", "low", "medium", "high"),
        "gpt-5-mini": ("minimal", "low", "medium", "high"),
        "gpt-5-nano": ("minimal", "low", "medium", "high"),
        "gpt-5.2": ("none", "low", "medium", "high", "xhigh"),
        "gpt-5.4": ("none", "low", "medium", "high", "xhigh"),
        "gpt-5.4-mini": ("none", "low", "medium", "high", "xhigh"),
        "gpt-5.4-nano": ("none", "low", "medium", "high", "xhigh"),
    }

    for model_id, expected in expected_efforts.items():
        assert MODELS_BY_ID[model_id].reasoning is True
        assert get_supported_reasoning_efforts(model_id) == expected


def test_chat_next_gpt_5_4_has_longer_context_window():
    gpt_54 = MODELS_BY_ID["gpt-5.4"]

    assert gpt_54.max_tokens_in == 922000
    assert gpt_54.max_tokens_out == 128000

    usage = get_context_usage_display(1000, 500, "gpt-5.4")
    assert usage["max_tokens"] == 922000


def test_compaction_thresholds_are_model_specific():
    assert get_compaction_threshold_tokens("gpt-5.1") == 200000
    assert get_compaction_threshold_tokens("gpt-5.4") == 850000
    assert get_compaction_threshold_percentage("gpt-5.1") == 74
    assert get_compaction_threshold_percentage("gpt-5.4") == 93


def test_context_usage_display_returns_small_arc_for_small_usage():
    usage = get_context_usage_display(10000, 700, "gpt-5.4")

    assert usage["percentage"] == 1
    assert usage["stroke_dasharray"] == "0.4 37.3"


def test_context_usage_display_returns_full_arc_at_limit():
    usage = get_context_usage_display(922000, 128000, "gpt-5.4")

    assert usage["percentage"] == 100
    assert usage["stroke_dasharray"] == "37.7 0.0"


def test_context_usage_display_localizes_tooltip_labels_in_french():
    with override("fr"):
        usage = get_context_usage_display(
            500000,
            1000,
            "gpt-5.4",
            cached_tokens=500,
            reasoning_tokens=200,
        )

        assert _("Context window") in usage["tooltip_text"]
        assert _("Input") in usage["tooltip_text"]
        assert _("Output") in usage["tooltip_text"]
        assert _("tokens") in usage["display_text_long"]
        assert (
            _("Performance may degrade as the context window fills up")
            in usage["tooltip_text"]
        )


def test_context_usage_display_hides_breakdown_when_disabled():
    usage = get_context_usage_display(
        10000,
        700,
        "gpt-5.4",
        cached_tokens=500,
        reasoning_tokens=200,
        show_token_breakdown=False,
    )

    assert "Input:" not in usage["tooltip_text"]
    assert "Output:" not in usage["tooltip_text"]
    assert "10.7K / 922K" in usage["tooltip_text"]


def test_context_usage_display_respects_custom_near_limit_threshold():
    usage = get_context_usage_display(
        10000,
        700,
        "gpt-5.4",
        near_limit_threshold_pct=1,
    )

    assert usage["near_limit"] is True


def test_context_usage_display_uses_model_specific_compaction_threshold_by_default():
    usage = get_context_usage_display(200000, 0, "gpt-5.1")

    assert usage["near_limit"] is True


def test_chat_next_model_choices_include_reasoning_effort_metadata():
    metadata_by_model_id = _choice_metadata_by_model_id()

    assert (
        metadata_by_model_id["gpt-5"]["supported-reasoning-efforts"]
        == "minimal,low,medium,high"
    )
    assert (
        metadata_by_model_id["gpt-5.1"]["supported-reasoning-efforts"]
        == "none,low,medium,high"
    )
    assert (
        metadata_by_model_id["gpt-5-mini"]["supported-reasoning-efforts"]
        == "minimal,low,medium,high"
    )
    assert (
        metadata_by_model_id["gpt-5-nano"]["supported-reasoning-efforts"]
        == "minimal,low,medium,high"
    )
    assert (
        metadata_by_model_id["gpt-5.4"]["supported-reasoning-efforts"]
        == "none,low,medium,high,xhigh"
    )
    assert (
        metadata_by_model_id["gpt-5.4-mini"]["supported-reasoning-efforts"]
        == "none,low,medium,high,xhigh"
    )
    assert (
        metadata_by_model_id["gpt-5.4-nano"]["supported-reasoning-efforts"]
        == "none,low,medium,high,xhigh"
    )
    assert (
        metadata_by_model_id["gpt-5.2"]["supported-reasoning-efforts"]
        == "none,low,medium,high,xhigh"
    )


def test_chat_next_reasoning_effort_normalization_respects_model_support():
    assert normalize_reasoning_effort("gpt-5", "none") == "minimal"
    assert normalize_reasoning_effort("gpt-5", "xhigh") == "high"
    assert normalize_reasoning_effort("gpt-5.1", "minimal") == "none"
    assert normalize_reasoning_effort("gpt-5.1", "xhigh") == "high"
    assert normalize_reasoning_effort("gpt-5.4", "minimal") == "none"
    assert normalize_reasoning_effort("gpt-5.4-mini", "minimal") == "none"
    assert normalize_reasoning_effort("gpt-5.4-nano", "minimal") == "none"
    assert normalize_reasoning_effort("gpt-5.4", "xhigh") == "xhigh"
    assert normalize_reasoning_effort("gpt-5.4-nano", "xhigh") == "xhigh"
    assert normalize_reasoning_effort("gpt-5.2", "minimal") == "none"
    assert normalize_reasoning_effort("gpt-5-mini", "none") == "minimal"
    assert normalize_reasoning_effort("gpt-5.2", "xhigh") == "xhigh"
