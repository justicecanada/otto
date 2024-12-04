import pytest

from otto.models import Feedback, Pilot, SecurityLabel


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


@pytest.mark.django_db
def test_user_pilot_name(basic_user):
    user = basic_user(accept_terms=True)
    assert user.pilot_name == "N/A"

    pilot = Pilot.objects.create(user=user, name="Test Pilot")
    user.pilot = pilot
    user.save()
    assert user.pilot_name == "Test Pilot"


@pytest.mark.django_db
def test_feedback_status_display(basic_user, basic_feedback):
    user = basic_user(accept_terms=True)
    feedback = basic_feedback(user=user)

    assert feedback.status_display() == "New"


@pytest.mark.django_db
def test_feedback_feedback_type_display(basic_user, basic_feedback):
    user = basic_user(accept_terms=True)
    feedback = basic_feedback(user=user)

    assert feedback.feedback_type_display() == "Feedback"


@pytest.mark.django_db
def test_get_feedback_stats(basic_user, basic_feedback):
    user = basic_user(accept_terms=True)

    feedback = basic_feedback(user=user)
    feedback.save()
    feedback2 = basic_feedback(user=user)
    feedback2.status = "resolved"
    feedback2.feedback_type = "bug"
    feedback2.save()
    feedback3 = basic_feedback(user=user)
    feedback2.status = "new"
    feedback2.feedback_type = "question"
    feedback3.save()

    stats = Feedback.objects.get_feedback_stats()

    assert stats["total"] == 3
    assert stats["negative"] == 0
    assert stats["resolved"] == 1
    assert stats["most_active"]["app"] == "Otto"
    assert stats["most_active"]["feedback_count"] == 3
