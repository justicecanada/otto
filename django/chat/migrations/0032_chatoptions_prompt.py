# Generated by Django 5.1.1 on 2024-11-08 16:54

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0031_alter_preset_sharing_option"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatoptions",
            name="prompt",
            field=models.TextField(blank=True),
        ),
    ]