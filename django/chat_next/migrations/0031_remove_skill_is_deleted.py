from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("chat_next", "0030_externaltoolapprovallog_pii_entity_categories"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="skill",
            name="is_deleted",
        ),
    ]
