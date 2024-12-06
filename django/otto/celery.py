import os
from logging.config import dictConfig

from django.conf import settings

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging
from django_structlog.celery.steps import DjangoStructLogInitStep

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "otto.settings")

app = Celery("otto")
# initialize django-structlog
app.steps["worker"].add(DjangoStructLogInitStep)
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    # Sync entra users every day at 1 am UTC
    "sync-entra-users-every-morning": {
        "task": "otto.tasks.sync_users",
        "schedule": crontab(hour=1, minute=0),
    },
    # Update laws every week on Saturdays at 5 am UTC
    "update-laws-every-week": {
        "task": "otto.tasks.update_laws",
        "schedule": crontab(hour=5, minute=0, day_of_week=6),
    },
    # Reset weekly bonus every Sunday at 12 am UTC
    "reset-weekly-bonus-every-sunday": {
        "task": "otto.tasks.reset_weekly_bonus",
        "schedule": crontab(hour=0, minute=0, day_of_week=0),
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
    # Delete unused libraries every day at 3 am UTC
    # Actually let's NOT do this yet. Too risky. Needs clearer definition of "unused"
    # "delete-unused-libraries-every-morning": {
    #     "task": "otto.tasks.delete_unused_libraries",
    #     "schedule": crontab(hour=3, minute=0),
    # },
    "delete-text-extractor-files-every-day": {
        "task": "otto.tasks.delete_text_extractor_files",
        "schedule": crontab(hour=0, minute=0),
    },
    "cleanup-vector-store-every-morning": {
        "task": "otto.tasks.cleanup_vector_store",
        "schedule": crontab(hour=3, minute=0),
    },
    # Update USD to CAD exchange rate every Sunday at 2 am UTC
    "update-exchange-rate-every-week": {
        "task": "otto.tasks.update_exchange_rate",
        "schedule": crontab(hour=2, minute=0, day_of_week=6),
    },
}


@setup_logging.connect
def config_loggers(*args, **kwargs):
    dictConfig(settings.LOGGING)
