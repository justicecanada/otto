from modeltranslation.translator import TranslationOptions, register

from .models import Law


@register(Law)
class CostTypeTranslationOptions(TranslationOptions):
    fields = (
        "title",
        "short_title",
        "long_title",
        "ref_number",
        "enabling_authority",
        "node_id",
    )
