from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cefr', '0006_add_cefr_listening_question_types_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='cefrlisteningsection',
            name='image',
            field=models.ImageField(
                blank=True,
                help_text='Optional image shown for Part 4 (image left, questions right layout)',
                null=True,
                upload_to='cefr/listening/',
            ),
        ),
    ]
