# Generated by Django 5.1 on 2024-10-29 15:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chronology_email", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="email",
            name="preview_context",
            field=models.TextField(blank=True, null=True),
        ),
    ]
