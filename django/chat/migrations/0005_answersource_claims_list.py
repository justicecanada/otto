# Generated by Django 5.1.5 on 2025-02-20 22:02

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0004_alter_message_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="answersource",
            name="claims_list",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
