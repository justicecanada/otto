# Generated by Django 5.1 on 2024-08-14 17:50

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("librarian", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="library",
            name="is_default_library",
            field=models.BooleanField(default=False),
        ),
    ]