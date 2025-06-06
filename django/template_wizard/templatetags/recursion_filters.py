from django import template

register = template.Library()


@register.filter
def is_list(value):
    return isinstance(value, (list, tuple))


@register.filter
def is_dict(value):
    return isinstance(value, dict)
