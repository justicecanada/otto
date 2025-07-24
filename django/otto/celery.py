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
    # NOTE: This is now handled by kubernetes CronJob instead
    # "update-laws-every-week": {
    #     "task": "laws.tasks.update_laws",
    #     "schedule": crontab(hour=5, minute=0, day_of_week=6),
    # },
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
    # Delete transcriber uploads every day at 2 am UTC
    "cleanup-transcriber-uploads-every-morning": {
        "task": "otto.tasks.cleanup_transcriber_uploads",
        "schedule": crontab(hour=2, minute=0),
    },
    # Delete empty template sessions every day at 2:05 am UTC
    "delete-empty-template-sessions-every-morning": {
        "task": "otto.tasks.delete_empty_template_sessions",
        "schedule": crontab(hour=2, minute=5),
    },
    # Reset User.accepted_terms_date daily for users who have accepted terms >= 30 days ago
    "reset-accepted-terms-date-every-month": {
        "task": "otto.tasks.reset_accepted_terms_date",
        "schedule": crontab(hour=0, minute=40),
    },
}


@setup_logging.connect
def config_loggers(*args, **kwargs):
    dictConfig(settings.LOGGING)
