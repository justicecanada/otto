from django.db import migrations, models


def set_document_provenance(apps, schema_editor):
    Document = apps.get_model("librarian", "Document")

    # Generated outputs in chat_next live as bot-only attachments and are not
    # source-linked via chat_next_messages.
    Document.objects.filter(
        chat_next_files__message__is_bot=True,
        chat_next_files__message__isnull=False,
        chat_next_messages__isnull=True,
        messages__isnull=True,
    ).update(provenance="generated_output")

    # URL retrieval source documents are explicit URLs.
    Document.objects.filter(url__isnull=False).exclude(url="").exclude(
        provenance="generated_output"
    ).update(provenance="url_retrieval")

    # Remaining documents with a saved file are treated as user uploads/source files.
    Document.objects.filter(saved_file__isnull=False, provenance="unknown").update(
        provenance="user_upload"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("librarian", "0021_libraryteamrole"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="provenance",
            field=models.CharField(
                choices=[
                    ("unknown", "Unknown"),
                    ("user_upload", "User upload"),
                    ("url_retrieval", "URL retrieval"),
                    ("generated_output", "Generated output"),
                ],
                db_index=True,
                default="unknown",
                max_length=32,
            ),
        ),
        migrations.RunPython(set_document_provenance, migrations.RunPython.noop),
    ]
