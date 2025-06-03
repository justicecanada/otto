from django.db import migrations
from django.utils.text import slugify
import re


def gen_slug(field_name):
    base_slug = slugify(field_name).replace("-", "_")
    return re.sub(r"[^\w]", "", base_slug) or "field"


def populate_slugs(apps, schema_editor):
    TemplateField = apps.get_model("template_wizard", "TemplateField")
    for field in TemplateField.objects.all():
        if not field.slug:
            field.slug = gen_slug(field.field_name)
            field.save()


class Migration(migrations.Migration):
    dependencies = [
        ("template_wizard", "0010_templatefield_slug"),
    ]
    operations = [
        migrations.RunPython(populate_slugs, reverse_code=migrations.RunPython.noop),
    ]
