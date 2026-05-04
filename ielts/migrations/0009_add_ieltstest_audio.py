from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ielts', '0008_add_listening_question_types_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='ieltstest',
            name='audio_file',
            field=models.FileField(blank=True, upload_to='ielts/audio/tests/'),
        ),
        migrations.AddField(
            model_name='ieltstest',
            name='audio_url',
            field=models.URLField(blank=True, max_length=500),
        ),
    ]
