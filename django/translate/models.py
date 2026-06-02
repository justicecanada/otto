from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

from otto.secure_models import SecureModel

LANGUAGE_CHOICES = [
    ("en", "English"),
    ("fr", "French"),
]


class UserRequest(SecureModel):
    name = models.CharField(max_length=255, default="Untitled request")
    created_at = models.DateTimeField(auto_now_add=True)
    source_lang = models.CharField(
        max_length=10, default="en", choices=LANGUAGE_CHOICES
    )
    target_lang = models.CharField(
        max_length=10, default="fr", choices=LANGUAGE_CHOICES
    )

    def __str__(self):
        return f"{self.name} - {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}"


class InputFile(SecureModel):
    """Stores uploaded files before translation processing"""

    file = models.FileField(upload_to="translate_input_files/")
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
    file = models.FileField(upload_to="translate_output_files/", blank=True, null=True)
    file_name = models.TextField(default="tmp")
    user_request = models.ForeignKey(
        UserRequest, related_name="output_files", on_delete=models.CASCADE
    )
    celery_task_ids = models.JSONField(default=list, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    usd_cost = models.FloatField(default=0.0)

    def get_permission_parents(self):
        return [self.user_request]


@receiver(post_delete, sender=OutputFile)
def output_file_post_delete(sender, instance, **kwargs):
    """Delete the physical file when OutputFile is deleted"""
    if instance.file:
        instance.file.delete(save=False)
