# Create your models here.
from django.db import models

from otto.secure_models import SecureModel


class UserRequest(SecureModel):
    name = models.CharField(max_length=255, default="Untitled request")
    created_at = models.DateTimeField(auto_now_add=True)
    merged = models.BooleanField(default=False)
    access_controls = models.ManyToManyField(
        "otto.AccessControl",
        related_name="lex_experiment_userrequest_controls",  # Unique related_name
        blank=True,
    )

    def __str__(self):
        return f"{self.name} - {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}"


class OutputFile(SecureModel):
    pdf_file = models.FileField(upload_to="lex_output_files/", blank=True, null=True)
    txt_file = models.FileField(upload_to="lex_output_files/", blank=True, null=True)
    usd_cost = models.FloatField(default=0.0)
    # Appropriate extension will be appended to the filename when downloaded
    file_name = models.TextField(default="tmp")
    user_request = models.ForeignKey(
        UserRequest, related_name="output_files", on_delete=models.CASCADE
    )
    celery_task_ids = models.JSONField(default=list, blank=True, null=True)
    access_controls = models.ManyToManyField(
        "otto.AccessControl",
        related_name="lex_experiment_outputfile_controls",  # Unique related_name
        blank=True,
    )

    def get_permission_parents(self):
        return [self.user_request]