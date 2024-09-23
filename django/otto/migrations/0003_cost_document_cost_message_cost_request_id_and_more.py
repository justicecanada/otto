# Generated by Django 5.1 on 2024-09-03 20:32

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0013_rename_cost_message_usd_cost"),
        ("librarian", "0005_rename_cost_document_usd_cost"),
        ("otto", "0002_costtype_feature_short_name_cost"),
    ]

    operations = [
        migrations.AddField(
            model_name="cost",
            name="document",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="librarian.document",
            ),
        ),
        migrations.AddField(
            model_name="cost",
            name="message",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="chat.message",
            ),
        ),
        migrations.AddField(
            model_name="cost",
            name="request_id",
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AlterField(
            model_name="cost",
            name="feature",
            field=models.CharField(
                blank=True,
                choices=[
                    ("librarian", "Librarian"),
                    ("qa", "Q&A"),
                    ("chat", "Chat"),
                    ("translate", "Translate"),
                    ("summarize", "Summarize"),
                    ("template_wizard", "Template wizard"),
                    ("laws_query", "Legislation search"),
                    ("laws_load", "Legislation loading"),
                    ("case_prep", "Case prep assistant"),
                    ("text_extractor", "Text extractor"),
                ],
                max_length=50,
                null=True,
            ),
        ),
    ]