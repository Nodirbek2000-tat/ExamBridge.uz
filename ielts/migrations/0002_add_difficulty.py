from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ielts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='readingpassage',
            name='difficulty',
            field=models.CharField(
                choices=[('EASY', 'Easy'), ('MEDIUM', 'Medium'), ('HARD', 'Hard')],
                default='MEDIUM',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='listeningsection',
            name='difficulty',
            field=models.CharField(
                choices=[('EASY', 'Easy'), ('MEDIUM', 'Medium'), ('HARD', 'Hard')],
                default='MEDIUM',
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name='listeningsection',
            name='audio_file',
            field=models.FileField(blank=True, upload_to='ielts/audio/'),
        ),
    ]
