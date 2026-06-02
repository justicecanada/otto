import otto.tasks  # noqa
import chat_next.tasks  # noqa

# Import the app from your /workspace/django/otto/celery.py file
from otto.celery import app
from otto.celery import _is_gevent_pool_enabled, _prepare_gevent_worker_environment

from django.apps import apps


# We need to import the tasks modules here to make sure they are registered
# for the test suite.
import laws.tasks  # noqa


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
        assert task_name in registered_tasks, (
            f"Task '{task_name}' in schedule '{schedule_name}' is not registered in Celery."
        )

        # 2. Optional: Check if the task belongs to an installed Django app
        app_name = task_name.split(".")[0]
        assert apps.is_installed(app_name), (
            f"The app '{app_name}' for task '{task_name}' is not in INSTALLED_APPS."
        )


def test_is_gevent_pool_enabled_detects_cli_flag():
    assert _is_gevent_pool_enabled(
        argv=["celery", "-A", "otto", "worker", "--pool=gevent"],
        environ={},
    )


def test_is_gevent_pool_enabled_detects_split_pool_flag():
    assert _is_gevent_pool_enabled(
        argv=["celery", "-A", "otto", "worker", "--pool", "gevent"],
        environ={},
    )


def test_is_gevent_pool_enabled_detects_short_pool_flag():
    assert _is_gevent_pool_enabled(
        argv=["celery", "-A", "otto", "worker", "-P", "gevent"],
        environ={},
    )


def test_is_gevent_pool_enabled_detects_environment_variable():
    assert _is_gevent_pool_enabled(
        argv=["celery", "-A", "otto", "worker"], environ={"CELERY_POOL": "gevent"}
    )


def test_is_gevent_pool_enabled_ignores_non_worker_contexts():
    assert not _is_gevent_pool_enabled(
        argv=["pytest", "-k", "gevent"],
        environ={"CELERY_POOL": "gevent"},
    )


def test_is_gevent_pool_enabled_ignores_non_gevent_workers():
    assert not _is_gevent_pool_enabled(
        argv=["celery", "-A", "otto", "worker", "--pool=prefork"],
        environ={"CELERY_POOL": "prefork"},
    )


def test_is_gevent_pool_enabled_requires_worker_for_environment_variable():
    assert not _is_gevent_pool_enabled(
        argv=["celery", "-A", "otto", "beat"],
        environ={"CELERY_POOL": "gevent"},
    )


def test_prepare_gevent_worker_environment_sets_async_unsafe_flag():
    environ = {}

    _prepare_gevent_worker_environment(environ)

    assert environ["DJANGO_ALLOW_ASYNC_UNSAFE"] == "true"


def test_prepare_gevent_worker_environment_preserves_existing_value():
    environ = {"DJANGO_ALLOW_ASYNC_UNSAFE": "custom"}

    _prepare_gevent_worker_environment(environ)

    assert environ["DJANGO_ALLOW_ASYNC_UNSAFE"] == "custom"


def test_celery_resilience_settings_enabled():
    assert app.conf.task_track_started is True
    assert app.conf.worker_soft_shutdown_timeout == 30
    assert app.conf.worker_enable_soft_shutdown_on_idle is True
