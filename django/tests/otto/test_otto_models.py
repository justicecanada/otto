from django.conf import settings
from django.contrib.auth.models import Group

import pytest

from otto.models import Feedback, OttoStatus, SecurityLabel


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


@pytest.mark.django_db
def test_otto_status_singleton_outside_request_context_is_quiet(capsys):
    status = OttoStatus.objects.singleton()

    assert status.pk == 1
    assert capsys.readouterr().out == ""


@pytest.mark.django_db
def test_user_is_admin_outside_request_context_is_quiet(all_apps_user, capsys):
    user = all_apps_user()
    admin_group, _ = Group.objects.get_or_create(name=settings.OTTO_ADMIN_GROUP)
    user.groups.add(admin_group)

    assert user.is_admin is True
    assert capsys.readouterr().out == ""
