import time

from django.core.cache import cache
from django.core.management import call_command

from celery import shared_task


@shared_task
def sync_users():
    call_command("sync_users")


@shared_task
def update_laws():
    call_command("load_laws_xml", "--force_download", "--full")


@shared_task
def reset_monthly_bonus():
    from otto.models import User

    User.objects.update(monthly_bonus=0)


@shared_task
def delete_old_chats():
    call_command("delete_old_chats")


@shared_task
def delete_empty_chats():
    call_command("delete_empty_chats")


@shared_task
def delete_unused_libraries():
    call_command("delete_unused_libraries")


@shared_task
def delete_old_libraries():
    call_command("delete_old_libraries")


@shared_task
def delete_text_extractor_files():
    call_command("delete_text_extractor_files")


@shared_task
def cleanup_vector_store():
    call_command("cleanup_vector_store")


@shared_task
def update_exchange_rate():
    call_command("update_exchange_rate")


# LOAD TESTING TASKS


@shared_task
def sleep_seconds(seconds):
    print("Sleeping for", seconds, "seconds")
    time.sleep(seconds)
    return True
