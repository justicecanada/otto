# Generated by Django 5.1 on 2024-08-13 18:09

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0005_alter_chatoptions_chat_system_prompt"),
    ]

    operations = [
        migrations.AlterField(
            model_name="chatoptions",
            name="chat_model",
            field=models.CharField(default="gpt-4o", max_length=255),
        ),
        migrations.AlterField(
            model_name="chatoptions",
            name="qa_model",
            field=models.CharField(default="gpt-4o", max_length=255),
        ),
        migrations.AlterField(
            model_name="chatoptions",
            name="summarize_model",
            field=models.CharField(default="gpt-4o", max_length=255),
        ),
    ]