from modeltranslation.translator import TranslationOptions, register

from .models import App, CostType, Feature, Notification


@register(App)
class AppTranslationOptions(TranslationOptions):
    fields = ("name",)


@register(Feature)
class FeatureTranslationOptions(TranslationOptions):
    fields = ("name", "description")


@register(CostType)
class CostTypeTranslationOptions(TranslationOptions):
    fields = ("name", "description", "unit_name")


@register(Notification)
class NotificationTranslationOptions(TranslationOptions):
    fields = ("heading", "text")
