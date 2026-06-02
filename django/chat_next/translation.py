from modeltranslation.translator import TranslationOptions, register

from .models import Skill, SkillTag


@register(Skill)
class SkillTranslationOptions(TranslationOptions):
    fields = ("display_name", "description", "short_description", "body")


@register(SkillTag)
class SkillTagTranslationOptions(TranslationOptions):
    fields = ("name",)
