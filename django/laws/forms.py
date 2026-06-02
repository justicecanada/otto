from django import forms
from django.conf import settings
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from autocomplete import HTMXAutoComplete
from autocomplete.widgets import Autocomplete

from otto.form_fields import PermissiveModelMultipleChoiceField
from otto.forms import SimpleFieldAutocompleteMixin

from chat._llm.models import get_grouped_chat_model_choices
from chat.forms import SelectWithOptionClasses

from .models import Law
from .prompts import default_additional_instructions


class ActsAutocomplete(SimpleFieldAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete component to select Acts only (filter out regulations)"""

    name = "enabling_acts"
    multiselect = True
    minimum_search_length = 0
    model = Law
    field_name = "title"

    def get_items(self, search=None, values=None, request=None):
        # Override to add type filter for acts only
        if values is not None:
            data = Law.objects.filter(type="act", id__in=values).values("id", "title")
            return [{"label": x["title"], "value": str(x["id"])} for x in data]

        if search is not None:
            if search == "":
                data = (
                    Law.objects.filter(type="act")
                    .values("id", "title")
                    .order_by("title")[: self.limit]
                )
            else:
                data = (
                    Law.objects.filter(type="act", title__icontains=search)
                    .values("id", "title")
                    .order_by("title")[: self.limit]
                )
            return [{"label": x["title"], "value": str(x["id"])} for x in data]

        return []


class LawsAutocomplete(SimpleFieldAutocompleteMixin, HTMXAutoComplete):
    """Autocomplete component to select any law (Act or Regulation)"""

    name = "laws"
    multiselect = True
    minimum_search_length = 0
    model = Law
    field_name = "title"
    order_by = "type,title"  # Custom ordering

    def get_items(self, search=None, values=None, request=None):
        # Override to use custom multi-field ordering
        if values is not None:
            data = Law.objects.filter(id__in=values).values("id", "title")
            return [{"label": x["title"], "value": str(x["id"])} for x in data]

        if search is not None:
            if search == "":
                data = Law.objects.values("id", "title").order_by("type", "title")[
                    : self.limit
                ]
            else:
                data = (
                    Law.objects.filter(title__icontains=search)
                    .values("id", "title")
                    .order_by("type", "title")[: self.limit]
                )
            return [{"label": x["title"], "value": str(x["id"])} for x in data]

        return []


class SelectWithModelGroups(SelectWithOptionClasses):
    def optgroups(self, name, value, attrs=None):
        groups = []
        for index, (group_label, options) in enumerate(self.choices):
            subgroup = []
            for option_value, option_label in options:
                selected = str(option_value) in value
                subgroup.append(
                    self.create_option(
                        name, option_value, option_label, selected, index
                    )
                )
            groups.append((group_label, subgroup, index))
        return groups


class LawSearchForm(forms.Form):
    """
    Enter query. Optionally, select query filters (e.g. specific laws, date ranges)
    and search/AI options (e.g. keyword ↔ vector ratio, number of sources, etc.)
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["model"] = forms.ChoiceField(
            label=_("AI model"),
            choices=get_grouped_chat_model_choices(),
            initial=settings.DEFAULT_LAWS_MODEL,
            widget=SelectWithModelGroups(attrs={"class": "form-select"}),
        )
        # Set detect_language label based on page language
        lang = get_language()
        if lang == "fr":
            self.fields["detect_language"].label = "Francais seulement"
        else:
            self.fields["detect_language"].label = "English only"

    # Select laws to search
    search_laws_option = forms.ChoiceField(
        choices=[
            ("all", _("All acts and regulations")),
            ("acts", _("All acts")),
            ("regulations", _("All regulations")),
            ("specific_laws", _("Specific act(s)/regulation(s)...")),
            ("enabling_acts", _("Enabled by specific act(s)...")),
        ],
        label=_("Select laws to search"),
        required=True,
        initial="all",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    laws = PermissiveModelMultipleChoiceField(
        queryset=Law.objects.none(),  # Queryset not used; autocomplete uses get_items()
        label=_("Select act(s)/regulation(s)"),
        required=False,
        widget=Autocomplete(
            use_ac=LawsAutocomplete,
            attrs={
                "component_id": "id_laws",
                "id": "id_laws__textinput",
            },
        ),
    )

    enabling_acts = PermissiveModelMultipleChoiceField(
        queryset=Law.objects.none(),  # Queryset not used; autocomplete uses get_items()
        label=_("Select enabling act(s)"),
        required=False,
        widget=Autocomplete(
            use_ac=ActsAutocomplete,
            attrs={
                "component_id": "id_enabling_acts",
                "id": "id_enabling_acts__textinput",
            },
        ),
    )

    # Select date filters
    date_filter_option = forms.ChoiceField(
        choices=[
            ("all", _("All dates")),
            ("filter_dates", _("Filter by section date metadata...")),
        ],
        label=_("Select date filters"),
        required=True,
        initial="all",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    in_force_date_start = forms.DateField(
        label=_("In force as of (start)"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    in_force_date_end = forms.DateField(
        label=_("In force as of (end)"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    last_amended_date_start = forms.DateField(
        label=_("Last amended (start)"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    last_amended_date_end = forms.DateField(
        label=_("Last amended (end)"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )

    # Language selection (All, English, French)
    language = forms.ChoiceField(
        choices=[
            ("all", _("All languages")),
            ("en", _("English only")),
            ("fr", _("French only")),
        ],
        label=_("Select language"),
        required=True,
        initial="all",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    # Search options
    vector_ratio = forms.FloatField(
        label=_("Keyword ↔ Vector"),
        min_value=0,
        max_value=1,
        initial=0.8,
        widget=forms.NumberInput(
            attrs={
                "type": "range",
                "class": "form-range",
                "step": "0.05",
                "style": "height: 2.5rem; margin-bottom:-20px !important; position: relative; display:block;",
            }
        ),
    )
    top_k = forms.IntegerField(
        label=_("Number of sources"),
        min_value=1,
        max_value=250,
        initial=25,
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )

    # AI answer options
    trim_redundant = forms.BooleanField(
        label=_("Trim redundant sources"),
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    additional_instructions = forms.CharField(
        label=_("Additional instructions for AI answer"),
        required=False,
        initial=default_additional_instructions,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "autocomplete": "off",
                "style": "height: 114px;",
            }
        ),
    )

    # Basic search fields
    query = forms.CharField(
        label=_("Query"),
        required=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control form-control-lg m-0",
                "id": "basic-search-input",
                "autocomplete": "off",
                "placeholder": _("Ask a question about federal legislation..."),
            }
        ),
    )
    ai_answer = forms.BooleanField(
        label=_("AI answer"),
        label_suffix="",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(
            attrs={"class": "form-check-input", "role": "switch", "id": "ai_answer"}
        ),
    )

    detect_language = forms.BooleanField(
        label=_("Detect language automatically"),
        label_suffix="",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(
            attrs={
                "class": "form-check-input",
                "role": "switch",
                "id": "detect_language",
            }
        ),
    )

    advanced = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.HiddenInput(attrs={"id": "advanced-toggle"}),
    )
