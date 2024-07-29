from django import forms

from .models import Session


class SessionForm(forms.ModelForm):
    class Meta:
        model = Session
        fields = ["name"]
        labels = {"name": "Session Name"}
