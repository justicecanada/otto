from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("chat_next", "0031_remove_skill_is_deleted"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="skill",
            name="name",
        ),
    ]
