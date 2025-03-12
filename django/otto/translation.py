from modeltranslation.translator import TranslationOptions, register

from .models import App, CostType, Feature, Notification, SecurityLabel


@register(App)
class AppTranslationOptions(TranslationOptions):
    fields = ("name",)


@register(Feature)
class FeatureTranslationOptions(TranslationOptions):
    fields = ("name", "description")


@register(SecurityLabel)
class SecurityLabelTranslationOptions(TranslationOptions):
    fields = ("name", "description", "acronym")


@register(CostType)
class CostTypeTranslationOptions(TranslationOptions):
    fields = ("name", "description", "unit_name")


@register(Notification)
class NotificationTranslationOptions(TranslationOptions):
    fields = ("heading", "text")
