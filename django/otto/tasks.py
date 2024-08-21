from django.core.management import call_command

from celery import shared_task


@shared_task
def sync_users():
    call_command("sync_users")
