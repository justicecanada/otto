from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.forms import ModelForm
from django.utils.translation import gettext_lazy as _

from autocomplete import widgets

from chat.models import Message, Preset
from otto.models import App, Feedback, Pilot

User = get_user_model()


class FeedbackForm(ModelForm):
    class Meta:
        model = Feedback

        fields = [
            "feedback_message",
            "modified_by",
            "created_by",
            "app",
            "chat_message",
            "otto_version",
            "url_context",
        ]

        widgets = {
            "feedback_message": forms.Textarea(
                attrs={
                    "id": "feedback-message-textarea",
                    "class": "form-control my-2",
                },
            ),
            "modified_by": forms.HiddenInput(),
            "created_by": forms.HiddenInput(),
            "chat_message": forms.HiddenInput(),
            "otto_version": forms.HiddenInput(),
            "url_context": forms.HiddenInput(),
            "app": forms.HiddenInput(),
        }

        labels = {
            "feedback_message": _(
                "Let us know what went wrong, or suggest an improvement."
            ),
        }

    def __init__(self, user, message_id, *args, **kwargs):
        super(FeedbackForm, self).__init__(*args, **kwargs)
        self.fields["created_by"].initial = user
        self.fields["modified_by"].initial = user
        self.fields["otto_version"].initial = settings.OTTO_VERSION_HASH

        if message_id is not None:
            self.fields["chat_message"].initial = message_id
            self.initialize_chat_feedback(message_id)
        else:
            self.fields["chat_message"].initial = ""
            self.fields["app"].initial = "Otto"

        self.fields["chat_message"].required = False

    def initialize_chat_feedback(self, message_id):
        if message_id:
            chat_mode = Message.objects.get(id=message_id).mode
            if chat_mode == "translate":
                self.fields["app"].initial = "translate"
            elif chat_mode == "summarize":
                self.fields["app"].initial = "summarize"
            elif chat_mode == "qa":
                self.fields["app"].initial = "qa"
            else:
                self.fields["app"].initial = "chat"

            self.fields["chat_message"].initial = message_id


class FeedbackMetadataForm(ModelForm):
    class Meta:
        model = Feedback
        fields = ["feedback_type", "status"]

        labels = {
            "feedback_type": _("Type"),
            "status": _("Status"),
        }

        widgets = {
            "feedback_type": forms.Select(
                attrs={"class": "form-select"},
                choices=Feedback.FEEDBACK_TYPE_CHOICES,
            ),
            "status": forms.Select(
                attrs={"class": "form-select"},
                choices=Feedback.FEEDBACK_STATUS_CHOICES,
            ),
        }


class FeedbackNoteForm(ModelForm):
    class Meta:
        model = Feedback
        fields = ["admin_notes"]

        labels = {
            "admin_notes": _("Add notes or addition details."),
        }

        widgets = {
            "admin_notes": forms.Textarea(
                attrs={
                    "class": "form-control fs-6",
                    "style": "height: 102px",
                    "placeholder": _("Add notes or addition details."),
                },
            ),
        }


# AC-16 & AC-16(2): Enables the modification of user roles and group memberships
class UserGroupForm(forms.Form):
    upn = forms.ModelMultipleChoiceField(
        queryset=User.objects.all().order_by("upn"),
        label="UPN",
        required=True,
        widget=widgets.Autocomplete(
            name="upn",
            options={
                "item_value": User.id,
                "item_label": User.upn,
                "multiselect": True,
                "minimum_search_length": 2,
                "model": User,
            },
        ),
    )
    group = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all(),
        label="Roles",
        required=False,
        widget=widgets.Autocomplete(
            name="group",
            options={"multiselect": True, "minimum_search_length": 0, "model": Group},
        ),
    )
    pilot = forms.ModelChoiceField(
        queryset=Pilot.objects.all(),
        label="Pilot",
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
        to_field_name="name",
    )
    monthly_max = forms.IntegerField(
        label="Monthly budget ($ CAD)",
        required=True,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
        initial=settings.DEFAULT_MONTHLY_MAX,
    )
    monthly_bonus = forms.IntegerField(
        label="Additional budget (this month only)",
        required=True,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
        initial=0,
    )


class PilotForm(forms.ModelForm):
    # Simple form with all the fields default widgets
    class Meta:
        model = Pilot
        fields = "__all__"
        # Add the bootstrap classes to the form fields and labels
        widgets = {
            "pilot_id": forms.TextInput(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "service_unit": forms.TextInput(
                attrs={"class": "form-control", "required": False}
            ),
            "description": forms.Textarea(
                attrs={"class": "form-control", "required": False, "rows": 3}
            ),
            "start_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date", "required": False}
            ),
            "end_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date", "required": False}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # For some reason, the required attribute is not being set in the widget
        # so we need to set it manually
        self.fields["start_date"].required = False
        self.fields["end_date"].required = False
        # If the instance is not None, then we are editing an existing pilot
        # So the pilot_id should be read-only
        if self.instance.pk:
            self.fields["pilot_id"].widget.attrs["readonly"] = True
            self.fields["pilot_id"].widget.attrs["disabled"] = True
