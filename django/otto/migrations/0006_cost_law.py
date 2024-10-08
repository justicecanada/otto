# Generated by Django 5.1 on 2024-09-16 13:05

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("laws", "0002_remove_law_lang_remove_law_law_id_and_more"),
        ("otto", "0005_alter_cost_date_incurred"),
    ]

    operations = [
        migrations.AddField(
            model_name="cost",
            name="law",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="laws.law",
            ),
        ),
    ]
