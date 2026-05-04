from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tests_app', '0003_bankquestion_category_question_type_savedbankquestion'),
    ]

    operations = [
        migrations.AddField(
            model_name='module',
            name='difficulty_variant',
            field=models.CharField(
                choices=[('STANDARD', 'Standard'), ('EASY', 'Easy (Adaptive)'), ('HARD', 'Hard (Adaptive)')],
                default='STANDARD',
                help_text='For Module 2: EASY or HARD variant (adaptive routing)',
                max_length=10,
            ),
        ),
    ]
