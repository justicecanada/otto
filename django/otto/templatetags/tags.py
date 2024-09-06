from django import template
from django.urls import reverse

register = template.Library()


@register.simple_tag
def get_librarian_modal_url(item_type, item_id):
    url_mapping = {
        "library": "librarian:modal_edit_library",
        "data_source": "librarian:modal_edit_data_source",
        "document": "librarian:modal_edit_document",
    }

    url_name = url_mapping.get(item_type)
    if url_name:
        return reverse(url_name, kwargs={f"{item_type}_id": item_id})
    return "#"


@register.simple_tag
def get_preset_description(preset, language):
    preset.get_description(language)
