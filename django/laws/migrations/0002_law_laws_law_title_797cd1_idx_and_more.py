# Generated by Django 5.1.1 on 2025-02-14 19:18

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("laws", "0001_initial"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="law",
            index=models.Index(fields=["title"], name="laws_law_title_797cd1_idx"),
        ),
        migrations.AddIndex(
            model_name="law",
            index=models.Index(fields=["type"], name="laws_law_type_84500f_idx"),
        ),
    ]
