from django.db import migrations, models


def populate_user_entra_status(apps, schema_editor):
    User = apps.get_model("otto", "User")
    User.objects.filter(is_active=True).update(entra_status="active")
    User.objects.filter(is_active=False).update(entra_status="unknown")


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0028_enable_organization_default_external_tool_review_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="entra_status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("disabled", "Disabled"),
                    ("deleted", "Deleted"),
                    ("unknown", "Unknown"),
                ],
                default="unknown",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="job_title",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="user",
            name="preferred_language",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.RunPython(
            populate_user_entra_status,
            migrations.RunPython.noop,
        ),
    ]
