# Generated by Django 5.1 on 2024-10-24 15:25

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("text_extractor", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="outputfile",
            name="file_id",
        ),
        migrations.AlterField(
            model_name="outputfile",
            name="file_name",
            field=models.TextField(default="tmp"),
        ),
    ]
