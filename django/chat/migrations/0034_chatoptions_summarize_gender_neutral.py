# Generated by Django 5.1.1 on 2024-11-13 16:56

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0033_alter_chatoptions_prompt"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatoptions",
            name="summarize_gender_neutral",
            field=models.BooleanField(default=True),
        ),
    ]
