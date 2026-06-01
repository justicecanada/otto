from modeltranslation.translator import TranslationOptions, register

from .models import CostGroup, CostType, Notification, SecurityLabel


@register(SecurityLabel)
class SecurityLabelTranslationOptions(TranslationOptions):
    fields = ("name", "description", "acronym")


@register(CostType)
class CostTypeTranslationOptions(TranslationOptions):
    fields = ("name", "description", "unit_name")


@register(CostGroup)
class CostGroupTranslationOptions(TranslationOptions):
    fields = ("name",)


@register(Notification)
class NotificationTranslationOptions(TranslationOptions):
    fields = ("heading", "text")
