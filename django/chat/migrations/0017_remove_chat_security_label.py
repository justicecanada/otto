from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0016_remove_chatoptions_qa_answer_mode_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="chat",
            name="security_label",
        ),
    ]
