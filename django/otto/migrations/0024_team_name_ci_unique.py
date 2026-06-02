from django.db import migrations, models
from django.db.models.functions import Lower


class Migration(migrations.Migration):

    dependencies = [
        ("otto", "0023_team_teammembership"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="team",
            constraint=models.UniqueConstraint(
                Lower("name"),
                name="otto_team_name_ci_unique",
            ),
        ),
    ]
