# Generated by Django 5.1.5 on 2025-03-07 16:16

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0004_alter_message_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="chat",
            name="last_modification_date",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
    ]
