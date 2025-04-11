# Generated by Django 5.1.8 on 2025-04-10 14:46

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("librarian", "0002_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="document",
            old_name="file",
            new_name="saved_file",
        ),
        migrations.AddField(
            model_name="document",
            name="file_path",
            field=models.TextField(blank=True, null=True),
        ),
    ]
