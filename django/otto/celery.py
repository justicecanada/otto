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
    # Sync entra users every day at 5 am UTC
    "sync-entra-users-every-morning": {
        "task": "otto.tasks.sync_users",
        "schedule": crontab(hour=5, minute=0),
    },
    # Update laws every week on Saturdays at 5 am UTC
    "update-laws-every-week": {
        "task": "otto.tasks.update_laws",
        "schedule": crontab(hour=5, minute=0, day_of_week=6),
    },
}


@setup_logging.connect
def config_loggers(*args, **kwargs):
    dictConfig(settings.LOGGING)
