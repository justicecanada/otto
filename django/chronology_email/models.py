import uuid

from django.db import models


class Email(models.Model):
    file = models.FileField(upload_to="emails/", null=True, blank=True)
    sender = models.CharField(max_length=255)
    receiver = models.CharField(max_length=255)
    sent_date = models.DateTimeField(null=True, blank=True)
    preview_text = models.TextField(null=True, blank=True)
    thread_id = models.UUIDField()
    attachment_count = models.IntegerField(default=0)  # New field for attachments

    def __str__(self):
        return f"{self.sender} to {self.receiver} on {self.sent_date}"


class Thread(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Thread {self.id}"
