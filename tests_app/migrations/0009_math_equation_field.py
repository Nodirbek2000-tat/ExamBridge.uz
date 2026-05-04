from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tests_app', '0008_bankquestion_passage_table_choice_images'),
    ]

    operations = [
        migrations.AddField(
            model_name='question',
            name='math_equation',
            field=models.TextField(blank=True, help_text='LaTeX equation shown centered above content, e.g. \\( ax^2+bx+c=0 \\)'),
        ),
        migrations.AddField(
            model_name='bankquestion',
            name='math_equation',
            field=models.TextField(blank=True, verbose_name='Math Equation', help_text='LaTeX tenglama, contentdan oldin markazda chiqadi'),
        ),
    ]
