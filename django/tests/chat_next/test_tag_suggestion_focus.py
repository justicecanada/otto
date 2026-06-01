import pytest
from chat_next._views.skills import (
    _count_close_token_matches,
    _max_edit_distance_for_term,
)


@pytest.mark.parametrize(
    "term,expected",
    [
        ("otto", 0),
        ("legal", 1),
        ("research", 1),
        ("translation", 2),
        ("communications", 2),
    ],
)
def test_max_edit_distance_for_term(term, expected):
    assert _max_edit_distance_for_term(term) == expected


def test_count_close_token_matches_requires_close_spelling():
    tag_tokens = {"translation"}
    close_content_tokens = {"translatoin", "workflow"}  # 2 edits away
    far_content_tokens = {"policy", "guidance"}

    assert _count_close_token_matches(tag_tokens, close_content_tokens) == 1
    assert _count_close_token_matches(tag_tokens, far_content_tokens) == 0


def test_count_close_token_matches_handles_multiple_tokens():
    tag_tokens = {"data", "analysis"}
    content_tokens = {"analysys", "dataset", "notes"}

    # "analysis" ~ "analysys" is close, "data" has no close match
    assert _count_close_token_matches(tag_tokens, content_tokens) == 1
