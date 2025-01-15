import uuid
from datetime import datetime

from django.db import models


class Thread(models.Model):
    id = models.UUIDField(default=uuid.uuid4, primary_key=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["created_at"])]

    def __str__(self):
        return f"Thread {self.id}"


class Email(models.Model):
    id = models.UUIDField(default=uuid.uuid4, primary_key=True, editable=False)
    thread = models.ForeignKey(
        Thread,
        on_delete=models.CASCADE,
        related_name="emails",
    )
    file = models.FileField(upload_to="emails/", null=True, blank=True)
    sent_date = models.DateTimeField(null=True, blank=True)
    sender = models.CharField(max_length=255)
    preview_text = models.TextField(null=True, blank=True)
    subject = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["created_at"])]

    def __str__(self):
        return f"{self.sender} on {self.sent_date}"


class Participant(models.Model):
    id = models.UUIDField(default=uuid.uuid4, primary_key=True, editable=False)
    email = models.ForeignKey(
        Email,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    email_address = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["created_at"])]


class Attachment(models.Model):
    id = models.UUIDField(default=uuid.uuid4, primary_key=True, editable=False)
    email = models.ForeignKey(
        Email, on_delete=models.CASCADE, related_name="attachments"
    )
    name = models.CharField(max_length=255)
    url = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    mime = models.CharField(max_length=30)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["created_at"])]
