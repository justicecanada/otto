# Create your models here.
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

from otto.secure_models import SecureModel


class UserRequest(SecureModel):
    name = models.CharField(max_length=255, default="Untitled request")
    created_at = models.DateTimeField(auto_now_add=True)
    merged = models.BooleanField(default=False)
    ai_model = models.CharField(max_length=64, default="document_intelligence")

    def __str__(self):
        return f"{self.name} - {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}"


class InputFile(SecureModel):
    """Stores uploaded files before OCR processing"""

    file = models.FileField(upload_to="text_extractor_input_files/")
    original_filename = models.CharField(max_length=500)
    content_type = models.CharField(max_length=100, blank=True)
    user_request = models.ForeignKey(
        UserRequest, related_name="input_files", on_delete=models.CASCADE
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def get_permission_parents(self):
        return [self.user_request]

    def __str__(self):
        return self.original_filename


@receiver(post_delete, sender=InputFile)
def input_file_post_delete(sender, instance, **kwargs):
    """Delete the physical file when InputFile is deleted"""
    if instance.file:
        instance.file.delete(save=False)


class OutputFile(SecureModel):
    pdf_file = models.FileField(upload_to="ocr_output_files/", blank=True, null=True)
    txt_file = models.FileField(upload_to="ocr_output_files/", blank=True, null=True)
    usd_cost = models.FloatField(default=0.0)
    # Appropriate extension will be appended to the filename when downloaded
    file_name = models.TextField(default="tmp")
    user_request = models.ForeignKey(
        UserRequest, related_name="output_files", on_delete=models.CASCADE
    )
    celery_task_ids = models.JSONField(default=list, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)

    def get_permission_parents(self):
        return [self.user_request]
