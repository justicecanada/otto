from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("librarian", "0023_alter_document_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="library",
            name="deleted_at",
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
    ]
