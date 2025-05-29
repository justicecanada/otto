from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from data_fetcher.util import get_request

from chat.models import SHARING_OPTIONS


class TemplateManager(models.Manager):
    pass


class Template(models.Model):
    objects = TemplateManager()

    # Metadata
    name_en = models.CharField(max_length=255, blank=True)
    name_fr = models.CharField(max_length=255, blank=True)
    description_en = models.TextField(blank=True)
    description_fr = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True
    )

    sharing_option = models.CharField(
        max_length=10,
        choices=SHARING_OPTIONS,
        default="private",
    )
    accessible_to = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="accessible_templates"
    )

    # Template content
    template_html = models.TextField()

    @property
    def shared_with(self):
        if self.sharing_option == "everyone":
            return _("Shared with everyone")
        elif self.sharing_option == "others":
            return _("Shared with others")
        return _("Private")

    @property
    def description_auto(self):
        request = get_request()
        if request and request.LANGUAGE_CODE == "fr":
            description = self.description_fr or self.description_en
        else:
            description = self.description_en or self.description_fr
        return description or _("No description available")

    def __str__(self):
        return f"Template {self.id}: {self.name_en}"

    @property
    def name_auto(self):
        request = get_request()
        if request and request.LANGUAGE_CODE == "fr":
            return self.name_fr or self.name_en
        else:
            return self.name_en or self.name_fr
