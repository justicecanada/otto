from django import forms

from .models import LANGUAGE_CHOICES


class DocumentTranslationForm(forms.Form):
    file = forms.FileField()
    language = forms.ChoiceField(choices=LANGUAGE_CHOICES, initial="fr")


class TextTranslationForm(forms.Form):
    source_text = forms.CharField(widget=forms.Textarea, required=False)
    source_lang = forms.ChoiceField(choices=LANGUAGE_CHOICES, initial="en")
    target_lang = forms.ChoiceField(choices=LANGUAGE_CHOICES, initial="fr")
