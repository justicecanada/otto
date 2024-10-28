from django.core.management import call_command

from celery import shared_task


@shared_task
def sync_users():
    call_command("sync_users")


@shared_task
def update_laws():
    call_command("load_laws_xml", "--force_download", "--full")


@shared_task
def reset_weekly_bonus():
    from otto.models import User

    User.objects.update(weekly_bonus=0)


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
def delete_text_extractor_files():
    call_command("delete_text_extractor_files")
