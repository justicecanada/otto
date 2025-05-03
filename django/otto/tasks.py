import time

from django.core.management import call_command

from celery import shared_task


@shared_task
def sync_users():
    call_command("sync_users")


@shared_task
def update_laws():
    call_command(
        "load_laws_xml", "--force_download", "--full", "--reset", "--accept_reset"
    )


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
def delete_translation_files():
    call_command("delete_translation_files")


@shared_task
def delete_unused_libraries():
    call_command("delete_unused_libraries")


@shared_task
def warn_libraries_pending_deletion():
    call_command("warn_libraries_pending_deletion")


@shared_task
def delete_text_extractor_files():
    call_command("delete_text_extractor_files")


@shared_task
def cleanup_vector_store():
    call_command("cleanup_vector_store")


@shared_task
def update_exchange_rate():
    call_command("update_exchange_rate")


@shared_task
def delete_tmp_upload_files():
    """
    Deletes temporary upload files that were never saved to the database (cancelled, etc.)
    https://mbraak.github.io/django-file-form/usage/
    """
    call_command("delete_unused_files")


@shared_task
def delete_dangling_savedfiles():
    from librarian.models import SavedFile

    for saved_file in SavedFile.objects.all():
        saved_file.safe_delete()


# LOAD TESTING TASKS


@shared_task
def sleep_seconds(seconds):
    print("Sleeping for", seconds, "seconds")
    time.sleep(seconds)
    return True
