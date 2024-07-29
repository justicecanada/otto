from django import template

register = template.Library()


@register.filter(name="addcss")
def addcss(field, cssArg):
    return field.as_widget(attrs={"class": cssArg})


@register.filter
def iso_date(value):
    if not value:
        return value
    return value.isoformat()
