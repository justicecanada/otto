from django import forms

from .models import Document, Session


class SessionForm(forms.ModelForm):
    class Meta:
        model = Session
        fields = ["name"]
        labels = {"name": "Session Name"}


class SessionDetailForm(forms.Form):
    COURT_CHOICES = [
        ("", "None"),
        ("Tax Court", "Tax Court"),
        ("Federal Court", "Federal Court"),
        ("Provincial Court", "Provincial Court"),
        ("Supreme Court", "Supreme Court"),
    ]

    court = forms.ChoiceField(choices=COURT_CHOICES, required=False)
    styleOfCause = forms.CharField(max_length=100, required=False, initial="none")
