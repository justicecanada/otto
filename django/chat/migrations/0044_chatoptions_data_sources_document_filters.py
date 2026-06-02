from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0043_update_translate_model_defaults"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatoptions",
            name="qa_additional_documents",
            field=models.ManyToManyField(
                blank=True,
                related_name="qa_additional_options",
                to="librarian.document",
            ),
        ),
        migrations.AddField(
            model_name="chatoptions",
            name="qa_excluded_documents",
            field=models.ManyToManyField(
                blank=True,
                related_name="qa_excluded_options",
                to="librarian.document",
            ),
        ),
    ]
