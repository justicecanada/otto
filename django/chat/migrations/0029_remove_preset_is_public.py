# Generated by Django 5.1 on 2024-10-28 21:34

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0028_alter_preset_sharing_option"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="preset",
            name="is_public",
        ),
    ]
