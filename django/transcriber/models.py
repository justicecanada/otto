from django.db import models
from django.utils import timezone

from celery.result import AsyncResult
from structlog import get_logger
from structlog.contextvars import bind_contextvars

from librarian.models import STATUS_CHOICES

logger = get_logger(__name__)


class Transcription(models.Model):
    """
    Result of adding a URL or uploading a file to the transcriber.
    """

    file_path = models.CharField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="PENDING"
    )  # e.g., Pending, In Progress, Completed, Error
    status_details = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)
    celery_task_id = models.CharField(max_length=255, null=True, blank=True)

    usd_cost = models.DecimalField(max_digits=10, decimal_places=6, default=0.0)

    extracted_title = models.CharField(max_length=255, null=True, blank=True)

    # Specific to URL-based transcriptions
    url = models.URLField(max_length=500, null=True, blank=True)
    fetched_at = models.DateTimeField(null=True, blank=True)
    url_content_type = models.CharField(max_length=255, null=True, blank=True)

    # Represents the WAV file associated with this transcription
    saved_file = models.ForeignKey(
        "WavFile",
        on_delete=models.CASCADE,
        related_name="transcriptions",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Transcription for {self.file_path} at {self.created_at}"

    @property
    def title(self):
        return self.extracted_title or None

    @property
    def name(self):
        return self.title or self.url or "Untitled transcription"

    @property
    def celery_status_message(self):
        """
        Returns a message based on the celery task status.
        """
        if self.celery_task_id:
            try:
                result = AsyncResult(self.celery_task_id)
                return result.info.get("status_text", "Processing...")
            except Exception as e:
                self.celery_task_id = None
                self.status = "Error"
                self.save()
            return "Error"
        return None

    @property
    def content_type(self):
        """
        Returns the content type of the saved file.
        """
        if self.saved_file:
            return self.saved_file.content_type
        else:
            return self.url_content_type

    def process(self):
        """
        Process the transcription, e.g., by calling a Celery task.
        """
        from transcriber.tasks import transcribe_wav_file

        bind_contextvars(transcription_id=self.id)

        if not (self.saved_file or self.url):
            self.status = "ERROR"
            self.save()
            return

        transcribe_wav_file.delay(self.saved_file.file.path, self.file_path)
        self.celery_task_id = "tbd"
        self.status = "INIT"
        self.save()

    def stop(self):
        """
        Stop the transcription process.
        """
        if self.celery_task_id:
            try:
                AsyncResult(self.celery_task_id).revoke(terminate=True)
            except Exception as e:
                logger.error("Error stopping transcription task", error=str(e))
            self.celery_task_id = "tbd"
            self.status = "INIT"
            self.save()


class WavFile(models.Model):
    """
    Represents a WAV file linked to a transcription.
    """

    sha256_hash = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    file = models.FileField(upload_to="transcriptions/%Y/%m/%d/", max_length=500)
    content_type = models.CharField(max_length=50, default="audio/wav")
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return (
            f"WavFile {self.file.name} linked to Transcription {self.transcription.id}"
        )

    def __str__(self):
        return self.file.name

    def generate_hash(self):
        """
        Generate a SHA256 hash for the file.
        """
        if self.file:
            import hashlib

            sha256 = hashlib.sha256()
            with self.file.open("rb") as f:
                while chunk := f.read(8192):
                    sha256.update(chunk)
            self.sha256_hash = sha256.hexdigest()
            self.save()
            print(f"Generated hash: {self.sha256_hash}")
        return self.sha256_hash

    def safe_delete(self):
        if self.transcriptions.exists():
            logger.info(
                f"Cannot delete WavFile {self.id} as it is linked to transcriptions."
            )
            return
        if self.file:
            self.file.delete(True)
        self.delete()
