from modeltranslation.translator import TranslationOptions, register

from chat.models import ChatOptions

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


@register(ChatOptions)
class ChatOptionsTranslationOptions(TranslationOptions):
    fields = (
        "chat_system_prompt",
        "qa_system_prompt",
        "qa_prompt_template",
        "qa_pre_instructions",
        "qa_post_instructions",
    )


# TODO: Seperate heading and text from model.

# @register(Notification)
# class NotificationTranslationOptions(TranslationOptions):
#     fields = ("heading", "text")
