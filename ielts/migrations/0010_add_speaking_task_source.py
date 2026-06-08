from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ielts', '0009_add_ieltstest_audio'),
    ]

    operations = [
        migrations.AddField(
            model_name='speakingtask',
            name='source',
            field=models.CharField(
                choices=[('IELTS', 'IELTS'), ('CEFR', 'CEFR')],
                default='IELTS',
                max_length=10,
            ),
        ),
    ]
