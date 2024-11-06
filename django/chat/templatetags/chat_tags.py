from django import template

register = template.Library()


@register.simple_tag
def get_preset_description(preset, language):
    return preset.get_description(language)
