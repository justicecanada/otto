# Generated by Django 5.1.1 on 2024-11-14 20:14

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0014_merge_20241104_1813"),
    ]

    operations = [
        migrations.CreateModel(
            name="OttoStatus",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("laws_last_refreshed", models.DateTimeField(blank=True, null=True)),
            ],
        ),
    ]