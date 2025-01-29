# Manual migration to reset accepted_terms_date to None for all otto.models.User

from django.db import migrations


def reset_accept_terms(apps, schema_editor):
    User = apps.get_model("otto", "User")
    User.objects.all().update(accepted_terms_date=None)


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0024_delete_usageterm"),
    ]

    operations = [
        migrations.RunPython(reset_accept_terms),
    ]
