from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from autocomplete import AutocompleteWidget, ModelAutocomplete, register

from chat.utils import bad_url
from librarian.models import DataSource, Document, Library, LibraryUserRole
from otto.utils.common import check_url_allowed

User = get_user_model()


class LibraryDetailForm(forms.ModelForm):
    template_name = "librarian/forms/library.html"

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super(LibraryDetailForm, self).__init__(*args, **kwargs)
        # A library can only be made public if it has an id (it is an existing library)
        # and the user has the change_publicity permission.
        self.can_make_public = self.user and self.user.has_perm(
            "librarian.change_publicity", self.instance
        )
        # If not, hide the is_public field
        if not self.can_make_public:
            self.fields.pop("is_public")
        else:
            # If the initial value of is_public is False, add an onclick property
            # to the is_public field to show the warning message
            if not self.instance.is_public:
                self.fields["is_public"].widget.attrs[
                    "onclick"
                ] = "toggleWarning(this);"
        # If an existing library, check if user has permissions to delete this library
        self.deletable = self.instance.pk and self.user.has_perm(
            "librarian.delete_library", self.instance
        )

    class Meta:
        model = Library
        fields = [
            "name_en",
            "name_fr",
            "description_en",
            "description_fr",
            "order",
            "is_public",
        ]
        widgets = {
            "name_en": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "name_fr": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "description_en": forms.Textarea(
                attrs={"class": "form-control form-control-sm", "rows": 2}
            ),
            "description_fr": forms.Textarea(
                attrs={"class": "form-control form-control-sm", "rows": 2}
            ),
            "order": forms.NumberInput(attrs={"class": "form-control form-control-sm"}),
            # AC-21: Add a checkbox to make the library public
            "is_public": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def save(self, commit=True):
        is_new = not self.instance.pk
        instance = super(LibraryDetailForm, self).save(commit=commit)
        if is_new:
            # Add the user as a library admin role
            LibraryUserRole.objects.create(
                library=instance, user=self.user, role="admin"
            )
            instance.reset()
        return instance


class DataSourceDetailForm(forms.ModelForm):
    template_name = "librarian/forms/data_source.html"

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.library_id = kwargs.pop("library_id", None)
        super(DataSourceDetailForm, self).__init__(*args, **kwargs)
        if self.library_id:
            self.fields["library"].initial = self.library_id
        self.deletable = self.instance.pk and self.user.has_perm(
            "librarian.delete_data_source", self.instance
        )

    class Meta:
        model = DataSource
        fields = ["name_en", "name_fr", "security_label", "order", "library"]
        widgets = {
            "library": forms.HiddenInput(),
            "name_en": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "name_fr": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "security_label": forms.Select(
                attrs={"class": "form-select form-select-sm"}
            ),
            "order": forms.NumberInput(attrs={"class": "form-control form-control-sm"}),
        }


class DocumentDetailForm(forms.ModelForm):
    template_name = "librarian/forms/document.html"

    def __init__(self, *args, **kwargs):
        self.data_source_id = kwargs.pop("data_source_id", None)
        super(DocumentDetailForm, self).__init__(*args, **kwargs)
        if self.data_source_id:
            self.fields["data_source"].initial = self.data_source_id
        # If there is a filename, hide the url and selector fields
        if self.instance.filename:
            self.fields.pop("url")
            self.fields.pop("selector")

    def clean_url(self):
        url = self.cleaned_data.get("url")
        if url:
            url_validator = URLValidator()
            try:
                url_validator(url)
            except ValidationError:
                raise ValidationError(_("Invalid URL"))
            if not check_url_allowed(url):
                raise ValidationError(mark_safe(bad_url(render_markdown=True)))
        return url

    class Meta:
        model = Document
        fields = ["manual_title", "url", "selector", "data_source", "filename"]
        widgets = {
            "data_source": forms.HiddenInput(),
            "filename": forms.HiddenInput(),
            "manual_title": forms.TextInput(
                attrs={"class": "form-control form-control-sm"}
            ),
            "url": forms.TextInput(attrs={"class": "form-control form-control-sm"}),
            "selector": forms.TextInput(
                attrs={"class": "form-control form-control-sm"}
            ),
        }


@register
class UserAutocomplete3(ModelAutocomplete):
    model = get_user_model()
    search_attrs = ["email"]
    minimum_search_length = 2
    name = "admins"


@register
class UserAutocomplete4(ModelAutocomplete):
    model = get_user_model()
    search_attrs = ["email"]
    minimum_search_length = 2
    name = "contributors"


@register
class UserAutocomplete5(ModelAutocomplete):
    model = get_user_model()
    search_attrs = ["email"]
    minimum_search_length = 2
    name = "viewers"


class LibraryUsersForm(forms.Form):
    template_name = "librarian/forms/library_users.html"

    admins = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(),
        label=_("Administrators (edit library and manage users)"),
        required=True,
        widget=AutocompleteWidget(
            ac_class=UserAutocomplete3,
            options={
                "multiselect": True,
            },
        ),
    )
    contributors = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(),
        label=_("Contributors (edit library)"),
        required=False,
        widget=AutocompleteWidget(
            ac_class=UserAutocomplete4,
            options={
                "multiselect": True,
            },
        ),
    )
    viewers = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(),
        label=_("Viewers (read-only access)"),
        required=False,
        widget=AutocompleteWidget(
            ac_class=UserAutocomplete5,
            options={
                "multiselect": True,
            },
        ),
    )

    def __init__(self, *args, **kwargs):
        self.library = kwargs.pop("library", None)
        super(LibraryUsersForm, self).__init__(*args, **kwargs)
        self.fields["admins"].initial = self.library.admins
        self.fields["contributors"].initial = self.library.contributors
        self.fields["viewers"].initial = self.library.viewers

    def save(self):
        self.library.user_roles.all().delete()

        for user in self.cleaned_data["admins"]:
            LibraryUserRole.objects.create(
                library=self.library, user=user, role="admin"
            )
        for user in self.cleaned_data["contributors"]:
            LibraryUserRole.objects.create(
                library=self.library, user=user, role="contributor"
            )
        for user in self.cleaned_data["viewers"]:
            LibraryUserRole.objects.create(
                library=self.library, user=user, role="viewer"
            )

    # Override clean function to check if the same user is in multiple roles
    def clean(self):
        cleaned_data = super(LibraryUsersForm, self).clean()
        user_roles_list = []
        user_roles_set = set()
        for role in ["admins", "contributors", "viewers"]:
            for user in cleaned_data[role]:
                user_roles_list.append(user)
                user_roles_set.add(user)
        if len(user_roles_list) != len(user_roles_set):
            raise forms.ValidationError(_("The same user cannot be in multiple roles."))
        return cleaned_data
