from django.db import migrations, models
from django.db.models import Q
from django.db.models.functions import Lower


class Migration(migrations.Migration):
    dependencies = [
        ("otto", "0020_alter_cost_feature"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="upn",
            field=models.CharField(max_length=255),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                Lower("upn"),
                condition=Q(is_active=True),
                name="otto_user_active_upn_ci_unique",
            ),
        ),
    ]
