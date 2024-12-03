# Generated by Django 5.1 on 2024-09-05 13:55

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0003_cost_document_cost_message_cost_request_id_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Pilot",
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
                ("pilot_id", models.CharField(max_length=50, unique=True)),
                ("name", models.CharField(max_length=100)),
                ("service_unit", models.TextField(blank=True, null=True)),
                ("description", models.TextField(blank=True, null=True)),
                ("start_date", models.DateField(null=True)),
                ("end_date", models.DateField(null=True)),
            ],
        ),
        migrations.AddField(
            model_name="user",
            name="pilot",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="otto.pilot",
            ),
        ),
    ]