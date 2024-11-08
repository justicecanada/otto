# Create your models here.
from django.db import models

from otto.secure_models import SecureModel


class UserRequest(SecureModel):
    name = models.CharField(max_length=255, default="Untitled request")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}"


class OutputFile(SecureModel):
    file = models.FileField(upload_to="ocr_output_files/")
    file_name = models.TextField(default="tmp")
    user_request = models.ForeignKey(
        UserRequest, related_name="output_files", on_delete=models.CASCADE
    )
    celery_task_ids = models.JSONField(default=list, blank=True, null=True)

    def get_permission_parents(self):
        return [self.user_request]
