# Generated by Django 5.1 on 2024-09-03 20:32

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("librarian", "0004_document_cost"),
    ]

    operations = [
        migrations.RenameField(
            model_name="document",
            old_name="cost",
            new_name="usd_cost",
        ),
    ]
