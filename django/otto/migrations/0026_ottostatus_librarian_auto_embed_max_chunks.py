from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0025_merge_20260331_2110"),
    ]

    operations = [
        migrations.AddField(
            model_name="ottostatus",
            name="librarian_auto_embed_max_chunks",
            field=models.IntegerField(
                blank=True,
                default=512,
                help_text="Pause librarian documents for manual approval when extracted chunk count exceeds this value. NULL disables the pause threshold.",
                null=True,
            ),
        ),
    ]
