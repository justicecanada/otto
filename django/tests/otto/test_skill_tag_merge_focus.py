import pytest

from otto.views import _max_merge_distance


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("otto", "otto", 0),
        ("legal", "legel", 1),
        ("policy", "polciy", 1),
        ("translation", "translatoin", 2),
        ("communications", "communicatons", 2),
    ],
)
def test_max_merge_distance_is_adaptive(a, b, expected):
    assert _max_merge_distance(a, b) == expected
