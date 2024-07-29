from modeltranslation.translator import TranslationOptions, register

from .models import DataSource, Library


@register(Library)
class LibraryTranslationOptions(TranslationOptions):
    fields = ("name", "description")


@register(DataSource)
class DataSourceTranslationOptions(TranslationOptions):
    fields = ("name",)
