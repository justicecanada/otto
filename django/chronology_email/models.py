from django.db import models


class EmailUpload(models.Model):
    email_file = models.FileField(upload_to="emails/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email_file.name
