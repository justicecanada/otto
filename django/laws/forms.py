from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from autocomplete import widgets

from chat.forms import CHAT_MODELS

from .models import Law


class LawSearchForm(forms.Form):
    """
    Enter query. Optionally, select query filters (e.g. specific laws, date ranges)
    and search/AI options (e.g. keyword ↔ vector ratio, number of sources, etc.)
    """

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

    laws = forms.ModelMultipleChoiceField(
        queryset=Law.objects.all(),
        label=_("Select act(s)/regulation(s)"),
        required=False,
        widget=widgets.Autocomplete(
            name="acts",
            options={
                "item_value": Law.id,
                "item_label": Law.title,
                "multiselect": True,
                "minimum_search_length": 0,
                "model": Law,
            },
        ),
    )

    enabling_acts = forms.ModelMultipleChoiceField(
        queryset=Law.objects.all(),
        label=_("Select enabling act(s)"),
        required=False,
        widget=widgets.Autocomplete(
            name="enabling_acts",
            options={
                "item_value": Law.id,
                "item_label": Law.title,
                "multiselect": True,
                "minimum_search_length": 0,
                "model": Law,
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
        label=_("In force (start)"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    in_force_date_end = forms.DateField(
        label=_("In force (end)"),
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

    # Search options
    vector_ratio = forms.FloatField(
        label=_("Keyword ↔ Vector"),
        min_value=0,
        max_value=1,
        initial=1,
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
        max_value=100,
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
    model = forms.ChoiceField(
        label=_("AI model"),
        choices=CHAT_MODELS,  # This will be populated dynamically in the view
        initial=settings.DEFAULT_CHAT_MODEL,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    context_tokens = forms.IntegerField(
        label=_("Max input tokens"),
        min_value=500,
        max_value=100000,
        initial=2000,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": 500}),
    )
    additional_instructions = forms.CharField(
        label=_("Additional instructions for AI answer"),
        required=False,
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
        initial=True,
        widget=forms.CheckboxInput(
            attrs={"class": "form-check-input", "role": "switch", "id": "ai_answer"}
        ),
    )
    bilingual_results = forms.BooleanField(
        label=_("Any language results"),
        label_suffix="",
        required=False,
        initial=False,
        widget=forms.CheckboxInput(
            attrs={
                "class": "form-check-input",
                "role": "switch",
                "id": "bilingual_results",
            }
        ),
    )
    advanced = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.HiddenInput(attrs={"id": "advanced-toggle"}),
    )
