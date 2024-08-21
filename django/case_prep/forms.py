from django import forms

from .models import Document, Session


class SessionForm(forms.ModelForm):
    class Meta:
        model = Session
        fields = ["name"]
        labels = {"name": "Session Name"}
