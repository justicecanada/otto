import traceback
import uuid
from datetime import datetime
from threading import Thread

from django.conf import settings
from django.utils.translation import gettext as _

from celery import current_task, shared_task
from celery.exceptions import SoftTimeLimitExceeded
from structlog import get_logger
from structlog.contextvars import get_contextvars
from transcriber.utils import transcribe_audio, transcribe_file

from .models import Transcription, WavFile

logger = get_logger(__name__)

ten_minutes = 600
one_minute = 60


@shared_task(soft_time_limit=ten_minutes)
def transcribe_audio_task(transcript_id, transcript_path):
    try:
        transcript = Transcription.objects.get(id=transcript_id)
    except Transcription.DoesNotExist:
        logger.error("Transcript not found", transcript_id=transcript_id)
        return

    try:
        process_transcription_helper(transcript, transcript_path)
    except Exception as e:
        transcript.status = "ERROR"
        full_error = traceback.format_exc()
        error_id = str(uuid.uuid4())[:7]
        logger.error(
            f"Error processing transcript: {transcript.name}",
            transcript_id=transcript.id,
            error_id=error_id,
            error=full_error,
        )
        transcript.celery_task_id = None
        if settings.DEBUG:
            transcript.status_details = full_error + f" ({_('Error ID')}: {error_id})"
        else:
            transcript.status_details = f"({_('Error ID')}: {error_id})"
        transcript.save()


def process_transcription_helper(transcription, transcript_path):
    url = transcription.url
    file = transcription.saved_file
    if not (url or file):
        raise ValueError("URL or file is required")
    logger.info("Processing file", file=file)
    if current_task:
        current_task.update_state(
            state="PROCESSING",
            meta={
                "status_text": _("Transcribing file..."),
            },
        )
    transcribe_file(file, transcript_path)
    # Done!
    transcription.status = "SUCCESS"
    transcription.fetched_at = datetime.now()
    transcription.celery_task_id = None
    transcription.save()
