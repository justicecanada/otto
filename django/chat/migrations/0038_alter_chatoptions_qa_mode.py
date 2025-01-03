# Generated by Django 5.1.1 on 2024-11-22 02:22

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0037_remove_answersource_node_text_answersource_node_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="chatoptions",
            name="qa_mode",
            field=models.CharField(
                choices=[
                    ("rag", "Use top sources only (fast, cheap)"),
                    ("summarize", "Read each document separately  (slow, $$)"),
                    ("summarize_combined", "Read all documents at once (slowest, $$)"),
                ],
                default="rag",
                max_length=20,
            ),
        ),
    ]
