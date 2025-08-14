import pytest

from otto.models import Feedback, Pilot


@pytest.mark.django_db
def test_user_pilot_name(basic_user):
    user = basic_user(accept_terms=True)
    assert user.pilot_name == "N/A"

    pilot = Pilot.objects.create(user=user, name="Test Pilot")
    user.pilot = pilot
    user.save()
    assert user.pilot_name == "Test Pilot"


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
