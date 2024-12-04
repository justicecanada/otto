from django.utils import timezone

import pytest

from otto.forms import FeedbackForm
from otto.models import Feedback


@pytest.mark.django_db
def test_feedback_form_is_valid(all_apps_user):
    user = all_apps_user()
    date_and_time = timezone.now().strftime("%Y%m%d-%H%M%S")

    feedback_form = FeedbackForm(
        user,
        None,
        data={
            "feedback_type": Feedback.FEEDBACK_TYPE_CHOICES[0][0],
            "feedback_message": "Test Message",
            "app": "Otto",
            "modified_by": user,
            "created_by": user,
            "created_at": date_and_time,
            "modified_at": date_and_time,
            "otto_version": "v0",
        },
    )

    x = feedback_form.is_valid()
    assert x is True
