from django import forms

from .models import EmailUpload


class EmailUploadForm(forms.ModelForm):
    class Meta:
        model = EmailUpload
        fields = ["email_file"]
