from django.apps import apps

import pytest

# We need to import the tasks modules here to make sure they are registered
# for the test suite.
import laws.tasks  # noqa
import otto.tasks  # noqa

# Import the app from your /workspace/django/otto/celery.py file
from otto.celery import app


def test_celery_beat_schedule_loads_all_tasks():
    """
    Test that the Celery beat schedule is configured correctly and all
    scheduled tasks are registered.
    """
    # Ensure the beat schedule is loaded and is a dictionary
    assert app.conf.beat_schedule
    assert isinstance(app.conf.beat_schedule, dict)

    registered_tasks = app.tasks.keys()

    for schedule_name, schedule_details in app.conf.beat_schedule.items():
        task_name = schedule_details["task"]

        # 1. Check if the task is registered in the Celery app
        assert (
            task_name in registered_tasks
        ), f"Task '{task_name}' in schedule '{schedule_name}' is not registered in Celery."

        # 2. Optional: Check if the task belongs to an installed Django app
        app_name = task_name.split(".")[0]
        assert apps.is_installed(
            app_name
        ), f"The app '{app_name}' for task '{task_name}' is not in INSTALLED_APPS."
