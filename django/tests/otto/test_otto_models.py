import pytest

from otto.models import SecurityLabel


@pytest.mark.django_db
def test_maximumof():
    acronyms_full = ["UC", "PA", "PB"]
    acronyms_empty = []
    acronyms_with_random = ["UC", "PA", "PB", "ZZ"]

    assert SecurityLabel.maximum_of(acronyms_full) == SecurityLabel.objects.get(
        acronym_en="PB"
    )
    assert SecurityLabel.maximum_of(acronyms_with_random) == SecurityLabel.objects.get(
        acronym_en="PB"
    )
    assert SecurityLabel.maximum_of(acronyms_empty) == SecurityLabel.objects.get(
        acronym_en="UC"
    )
