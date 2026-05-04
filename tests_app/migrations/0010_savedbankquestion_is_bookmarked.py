from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tests_app', '0009_math_equation_field'),
    ]

    operations = [
        migrations.AddField(
            model_name='savedbankquestion',
            name='is_bookmarked',
            field=models.BooleanField(default=False),
        ),
    ]
