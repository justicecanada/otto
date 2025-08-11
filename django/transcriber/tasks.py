import os
import uuid
from datetime import datetime
from threading import Thread

from django.conf import settings

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from structlog import get_logger
from structlog.contextvars import get_contextvars
from transcriber.utils import transcribe_audio

logger = get_logger(__name__)

ten_minutes = 600
one_minute = 60


@shared_task(soft_time_limit=ten_minutes)
def transcribe_wav_file(file_path, transcript_path):
    try:
        logger.info(f"Processing transcription for {file_path} at {datetime.now()}")
        file_name = file_path.split("/")[-1]
        input_file_name = file_name.replace(" ", "_")

        file_uuid = uuid.uuid4()
        input_file_path = f"{settings.AZURE_STORAGE_TRANSCRIPTION_INPUT_URL_SEGMENT}/{file_uuid}/{input_file_name}"

        # Upload to Azure Blob Storage
        azure_storage = settings.AZURE_STORAGE
        with open(file_path, "rb") as f:
            azure_storage.save(input_file_path, f)

        transcribe_audio(input_file_path, transcript_path)

    except SoftTimeLimitExceeded:
        logger.error(f"Transcription task timed out for {file_path}")
        raise Exception(f"Transcription task timed out for {file_path}")
    except Exception as e:
        logger.exception(f"Error transcribing {file_path}: {e}")
        raise Exception(f"Error transcribing {file_path}")
    finally:
        if input_file_path:
            Thread(target=azure_delete, args=(input_file_path,)).start()


def azure_delete(path):
    azure_storage = settings.AZURE_STORAGE
    try:
        logger.info(f"Deleting {path} from azure storage.")
        azure_storage.delete(path)
        # Now delete the parent folder
        azure_storage.delete(path.rsplit("/", 1)[0])
    except:
        logger.error(f"Error deleting {path}")
        pass
