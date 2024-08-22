from modeltranslation.translator import TranslationOptions, register

from .models import App, CostType, Feature, SecurityLabel, UsageTerm


@register(App)
class AppTranslationOptions(TranslationOptions):
    fields = ("name",)


@register(Feature)
class FeatureTranslationOptions(TranslationOptions):
    fields = ("name", "description")


@register(UsageTerm)
class UsageTermTranslationOptions(TranslationOptions):
    fields = ("term_text",)


@register(SecurityLabel)
class SecurityLabelTranslationOptions(TranslationOptions):
    fields = ("name", "description", "acronym")


@register(CostType)
class CostTypeTranslationOptions(TranslationOptions):
    fields = ("name", "description", "unit_name")


# TODO: Seperate heading and text from model.

# @register(Notification)
# class NotificationTranslationOptions(TranslationOptions):
#     fields = ("heading", "text")
