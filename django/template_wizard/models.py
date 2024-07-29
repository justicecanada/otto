from django.db import models

from otto.secure_models import SecureModel


class Report(SecureModel):
    name = models.CharField(max_length=255, default="Untitled report")
    wizard = models.CharField(max_length=255)
    data = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
