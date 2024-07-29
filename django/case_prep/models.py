from django.contrib.auth import get_user_model
from django.db import models

from otto.secure_models import SecureModel, SecureRelatedModel

User = get_user_model()


class Session(SecureModel):
    name = models.CharField(max_length=255)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    book_of_documents = models.FileField(
        upload_to="case_prep/books/", null=True, blank=True
    )

    def __str__(self):
        return self.name

    def get_next_sequence_number(self):
        last_document = self.document_set.order_by("-sequence").first()
        if last_document:
            return last_document.sequence + 1
        else:
            return 1


class Document(SecureRelatedModel):
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    sequence = models.IntegerField()
    original_name = models.TextField(blank=True)
    name = models.CharField(max_length=255)
    date = models.DateField(null=True, blank=True)
    file = models.FileField(upload_to="case_prep/%Y/%m/%d/")
    content_type = models.CharField(max_length=255, blank=True)
    hidden = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.pk:
            self.sequence = self.session.get_next_sequence_number()
        super().save(*args, **kwargs)

    def get_permission_parents(self):
        return [self.session]

    def __str__(self):
        return self.name
