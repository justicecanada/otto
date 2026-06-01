# CRITICAL: Gevent monkey patching must be done BEFORE any other imports
# This makes all blocking I/O operations (file, network, DB) cooperative with gevent
import os
import sys


def _is_gevent_pool_enabled(argv=None, environ=None):
    """Return True when this process is starting a gevent-backed Celery worker."""
    argv = argv if argv is not None else sys.argv
    environ = environ if environ is not None else os.environ

    if "worker" not in argv:
        return False

    for index, arg in enumerate(argv):
        if arg == "--pool" and index + 1 < len(argv) and argv[index + 1] == "gevent":
            return True
        if arg == "-P" and index + 1 < len(argv) and argv[index + 1] == "gevent":
            return True
        if arg in {"--pool=gevent", "-Pgevent"}:
            return True

    return environ.get("CELERY_POOL") == "gevent"


def _prepare_gevent_worker_environment(environ=None):
    """Relax Django's async ORM guard for sync Celery gevent workers.

    Celery workers in this repo run synchronous Django ORM code under a gevent
    pool. Some libraries used by tasks can also spin up incidental asyncio loops
    on the worker thread, which causes Django to raise
    SynchronousOnlyOperation even though the task itself is not an async Django
    view/consumer. Setting this flag for the gevent worker process keeps sync ORM
    access working in that runtime.
    """
    environ = environ if environ is not None else os.environ
    environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _patch_gevent():
    from gevent import monkey

    monkey.patch_all()


# Only apply worker-specific gevent configuration when running under gevent pool
if _is_gevent_pool_enabled():
    _prepare_gevent_worker_environment()
    _patch_gevent()


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "otto.settings")


def _create_celery_app():
    from celery import Celery
    from celery.schedules import crontab
    from django_structlog.celery.steps import DjangoStructLogInitStep

    app = Celery("otto")
    # initialize django-structlog
    app.steps["worker"].add(DjangoStructLogInitStep)
    app.config_from_object("django.conf:settings", namespace="CELERY")

    app.conf.broker_transport_options = {
        "queue_order_strategy": "priority",
        # Redis has no native priority queue, so Kombu/Celery emulates priority by
        # splitting one logical queue into multiple Redis lists (priority buckets).
        #
        # WHAT THIS DOES
        # - "priority_steps": [0, 3, 6, 9] creates 4 buckets per queue.
        # - Priorities 0-9 are grouped into those buckets (coarse-grained priority),
        #   e.g. 0-2 -> 0, 3-5 -> 3, 6-8 -> 6, 9 -> 9.
        # - Concrete Redis keys therefore include:
        #   - base queue name (bucket 0), e.g. "light", "heavy"
        #   - suffixed buckets, e.g. "light\x06\x163", "light\x06\x166", "light\x06\x169"
        #
        # WHY WE KEEP IT THIS WAY
        # - Fewer buckets means fewer Redis keys and less broker/worker polling
        #   overhead than one bucket per integer priority.
        # - We keep steps explicit so KEDA ScaledObject listName triggers can track
        #   every concrete queue key in infrastructure/k8s/celery.yaml.
        #
        # ALTERNATIVES / TRADE-OFFS
        # - More fine-grained in Redis: use priority_steps=list(range(10)) to
        #   distinguish each priority value (more keys, more ops overhead, and KEDA
        #   trigger config must include all resulting queue keys).
        # - RabbitMQ transport: supports native per-message priority (x-max-priority),
        #   avoiding Redis-style bucket key fan-out; trade-off is operating RabbitMQ
        #   and migrating broker/infrastructure/monitoring.
        "priority_steps": [0, 3, 6, 9],
    }

    # Hard time limit: SIGKILL the worker process if a task is still running after
    # this many seconds. This is a backstop for tasks stuck in C extensions (e.g.
    # blocked on a Postgres/Azure socket) where the Python soft_time_limit signal
    # is never delivered. Set above the longest soft_time_limit in the codebase
    # (librarian: 1 hour), with a generous buffer.
    app.conf.task_time_limit = 7200  # 2 hours hard kill

    app.autodiscover_tasks()

    app.conf.beat_schedule = {
        # Sync entra users every day at 1 am UTC
        "sync-entra-users-every-morning": {
            "task": "otto.tasks.sync_users",
            "schedule": crontab(hour=1, minute=0),
        },
        "update-laws-every-week": {
            "task": "laws.tasks.update_laws",
            "schedule": crontab(hour=5, minute=0, day_of_week=6),
        },
        # Reset monthly bonus every month on the 1st at 12 am UTC
        "reset-monthly-bonus-every-month": {
            "task": "otto.tasks.reset_monthly_bonus",
            "schedule": crontab(hour=0, minute=0, day_of_month=1),
        },
        # Delete old chats (90 days retention) every day at 2 am UTC
        "delete-old-chats-every-morning": {
            "task": "otto.tasks.delete_old_chats",
            "schedule": crontab(hour=2, minute=0),
        },
        # Delete empty chats every day at 2 am UTC
        "delete-empty-chats-every-morning": {
            "task": "otto.tasks.delete_empty_chats",
            "schedule": crontab(hour=2, minute=0),
        },
        # Delete old law searches (30 days retention) every day at 2:15 am UTC
        "delete-old-law-searches-every-morning": {
            "task": "laws.tasks.delete_old_law_searches",
            "schedule": crontab(hour=2, minute=15),
        },
        # Delete dangling SavedFiles every day at 3:00 am UTC
        "delete-dangling-savedfiles-every-morning": {
            "task": "otto.tasks.delete_dangling_savedfiles",
            "schedule": crontab(hour=2, minute=30),
        },
        # Delete dangling azure translation files every day at 3:30 am UTC
        "delete-translation-files-every-morning": {
            "task": "otto.tasks.delete_translation_files",
            "schedule": crontab(hour=3, minute=30),
        },
        # Delete dangling upload temporary files every day at 4:00 am UTC
        "delete-temp-uploads-every-morning": {
            "task": "otto.tasks.delete_tmp_upload_files",
            "schedule": crontab(hour=4, minute=00),
        },
        # Delete old law temporary processing files every day at 4:15 am UTC
        "delete-laws-temp-files-every-morning": {
            "task": "otto.tasks.delete_laws_temp_files",
            "schedule": crontab(hour=4, minute=15),
        },
        # Delete unused libraries every day at 3 am UTC
        "delete-unused-libraries-every-morning": {
            "task": "otto.tasks.delete_unused_libraries",
            "schedule": crontab(hour=3, minute=0),
        },
        # Warn users of pending library deletion every day at 3 am UTC
        "warn-libraries-pending-deletion-every-morning": {
            "task": "otto.tasks.warn_libraries_pending_deletion",
            "schedule": crontab(hour=3, minute=0),
        },
        # Hard-delete libraries that were soft-deleted earlier in the day.
        "cleanup-deleted-libraries-every-morning": {
            "task": "otto.tasks.cleanup_deleted_libraries",
            "schedule": crontab(hour=3, minute=15),
        },
        "delete-text-extractor-files-every-day": {
            "task": "otto.tasks.delete_text_extractor_files",
            "schedule": crontab(hour=0, minute=0),
        },
        # NOTE: I am uncomfortable with this running without more tests / monitoring
        # "cleanup-vector-store-every-morning": {
        #     "task": "otto.tasks.cleanup_vector_store",
        #     "schedule": crontab(hour=3, minute=0),
        # },
        # Update USD to CAD exchange rate every Sunday at 2 am UTC
        "update-exchange-rate-every-week": {
            "task": "otto.tasks.update_exchange_rate",
            "schedule": crontab(hour=2, minute=0, day_of_week=6),
        },
        # Reset User.accepted_terms_date daily for users who have accepted terms >= 30 days ago
        "reset-accepted-terms-date-every-month": {
            "task": "otto.tasks.reset_accepted_terms_date",
            "schedule": crontab(hour=0, minute=40),
        },
        # Nightly HNSW maintenance: update totals and build missing indexes
        "optimize-libraries-nightly": {
            "task": "otto.tasks.optimize_libraries",
            # Run daily at 3:45 AM UTC, after deletion/cleanup jobs
            "schedule": crontab(hour=3, minute=45),
        },
        # Snapshot queue depth plus worker active/reserved counts during the day so
        # incidents have a rolling breadcrumb trail instead of a single sad graph.
        "log-celery-queue-snapshot-every-five-minutes": {
            "task": "otto.tasks.log_celery_queue_snapshot",
            "schedule": crontab(minute="*/5"),
            "options": {"queue": "heavy"},
        },
        "clearsessions": {
            "task": "otto.tasks.clearsessions",
            "schedule": crontab(hour=2, minute=0),
        },
        # Clean up dangling OpenAI files (uploaded but no longer referenced in DB)
        "cleanup-dangling-openai-files-nightly": {
            "task": "chat_next.tasks.cleanup_dangling_openai_files",
            "schedule": crontab(hour=4, minute=30),
        },
        # Clean up dangling OpenAI responses (stored but no longer referenced in DB)
        "cleanup-dangling-openai-responses-nightly": {
            "task": "chat_next.tasks.cleanup_dangling_openai_responses",
            "schedule": crontab(hour=4, minute=45),
        },
        # Clean up dangling transcription input blobs (orphaned by crashed/timed-out tasks)
        "cleanup-dangling-transcription-blobs-nightly": {
            "task": "chat_next.tasks.cleanup_dangling_transcription_blobs",
            "schedule": crontab(hour=5, minute=0),
        },
    }

    return app


def _connect_logging_signal():
    from logging.config import dictConfig

    from django.conf import settings

    from celery.signals import setup_logging

    @setup_logging.connect
    def config_loggers(*args, **kwargs):
        dictConfig(settings.LOGGING)

    return config_loggers


app = _create_celery_app()
config_loggers = _connect_logging_signal()
