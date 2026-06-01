import time
from datetime import date, timedelta

from django.conf import settings
from django.core.management import call_command

from celery import shared_task
from structlog import get_logger

logger = get_logger(__name__)


@shared_task
def sync_users():
    call_command("sync_users")


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
def cleanup_deleted_libraries():
    call_command("cleanup_deleted_libraries")


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


@shared_task
def reset_accepted_terms_date():
    from otto.models import User

    # Filter for users who have accepted terms at least 30 days ago
    cutoff_date = date.today() - timedelta(days=30)
    users = User.objects.filter(accepted_terms_date__lte=cutoff_date)
    for user in users:
        user.accepted_terms_date = None
        user.save(update_fields=["accepted_terms_date"])
        print(f"Reset accepted_terms_date for user {user.id}")


@shared_task
def clearsessions():
    call_command("clearsessions")


@shared_task
def delete_laws_temp_files():
    """
    Delete temporary law processing files older than 24 hours.
    These files should be cleaned up immediately after use, but this ensures
    orphaned files from crashes/failures are eventually removed.
    """
    import os
    from datetime import datetime, timedelta

    from django.conf import settings

    from structlog import get_logger

    logger = get_logger(__name__)

    temp_dir = os.path.join(settings.MEDIA_ROOT, "tmp_laws")
    if not os.path.exists(temp_dir):
        logger.info("Laws temp directory does not exist, skipping cleanup")
        return

    cutoff_time = datetime.now() - timedelta(hours=24)
    deleted_count = 0

    for filename in os.listdir(temp_dir):
        file_path = os.path.join(temp_dir, filename)
        if os.path.isfile(file_path):
            file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
            if file_mtime < cutoff_time:
                try:
                    os.unlink(file_path)
                    deleted_count += 1
                    logger.debug(f"Deleted old temp file: {filename}")
                except Exception as e:
                    logger.error(f"Failed to delete temp file {filename}: {e}")

    logger.info(f"Deleted {deleted_count} old law temp files")


@shared_task
def optimize_libraries(
    force: bool = False, dry_run: bool = False, library_id: int | None = None
):
    """Run the optimize_libraries management command.

    - Updates total_chunks for all (non-personal) libraries
    - Triggers HNSW index builds for libraries that should use them and are missing
    - Optionally force rebuilds or limit to a single library
    """
    args: list[str] = []
    if force:
        args.append("--force")
    if dry_run:
        args.append("--dry-run")
    if library_id is not None:
        args.extend(["--library-id", str(library_id)])

    call_command("optimize_libraries", *args)


@shared_task(queue=settings.HEAVY_QUEUE)
def log_celery_queue_snapshot():
    from otto.celery_diagnostics import (
        collect_celery_snapshot,
        summarise_celery_snapshot,
    )

    snapshot = collect_celery_snapshot()
    summary = summarise_celery_snapshot(snapshot)

    has_backlog = any(summary["queue_totals"].values())
    has_worker_activity = any(
        counts["active"] or counts["reserved"] or counts["scheduled"]
        for counts in summary["worker_counts"].values()
    )

    if (
        has_backlog
        or has_worker_activity
        or summary["redis_error"]
        or summary["inspect_error"]
    ):
        logger.info("Celery queue snapshot", **summary)

    return summary


# LOAD TESTING TASKS


@shared_task
def sleep_seconds(seconds):
    print("Sleeping for", seconds, "seconds")
    time.sleep(seconds)
    return True
