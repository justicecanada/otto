from chat._llm.models import (
    MODELS_BY_ID,
    get_supported_reasoning_efforts,
    normalize_reasoning_effort,
)


def test_chat_gpt_5_catalog_matches_live_reasoning_support():
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


def test_chat_reasoning_effort_normalization_respects_model_support():
    assert normalize_reasoning_effort("gpt-5", "none") == "minimal"
    assert normalize_reasoning_effort("gpt-5.1", "minimal") == "none"
    assert normalize_reasoning_effort("gpt-5.1", "xhigh") == "high"
    assert normalize_reasoning_effort("gpt-5.2", "xhigh") == "xhigh"
    assert normalize_reasoning_effort("gpt-5.4", "minimal") == "none"
    assert normalize_reasoning_effort("gpt-5.4-mini", "xhigh") == "xhigh"
    assert normalize_reasoning_effort("gpt-5.4-nano", "minimal") == "none"
    assert normalize_reasoning_effort("gpt-5-mini", "none") == "minimal"
