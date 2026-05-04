from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cefr', '0003_add_level_timelimit_to_reading_listening'),
    ]

    operations = [
        migrations.AddField(
            model_name='cefrreadingquestion',
            name='image',
            field=models.ImageField(blank=True, null=True, upload_to='cefr/reading/questions/'),
        ),
        migrations.AddField(
            model_name='cefrreadingquestion',
            name='word_bank',
            field=models.JSONField(blank=True, default=list, help_text='Word bank for SUMM type drag-and-drop'),
        ),
        migrations.AddField(
            model_name='cefrlisteningquestion',
            name='image',
            field=models.ImageField(blank=True, null=True, upload_to='cefr/listening/questions/'),
        ),
        migrations.AddField(
            model_name='cefrlisteningquestion',
            name='word_bank',
            field=models.JSONField(blank=True, default=list, help_text='Word bank for SUMM type drag-and-drop'),
        ),
    ]
