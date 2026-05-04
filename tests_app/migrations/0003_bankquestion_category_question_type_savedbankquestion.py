from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('tests_app', '0002_add_bank_question'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='bankquestion',
            name='category',
            field=models.CharField(blank=True, help_text='SAT category (algebra, advanced_math, etc.)', max_length=50, verbose_name='Kategoriya'),
        ),
        migrations.AddField(
            model_name='bankquestion',
            name='question_type',
            field=models.CharField(choices=[('MCQ', 'Multiple Choice'), ('INPUT', 'Student-Produced Response')], default='MCQ', max_length=10, verbose_name='Savol turi'),
        ),
        migrations.AlterField(
            model_name='bankquestion',
            name='choice_a',
            field=models.TextField(blank=True, verbose_name='A variant'),
        ),
        migrations.AlterField(
            model_name='bankquestion',
            name='choice_b',
            field=models.TextField(blank=True, verbose_name='B variant'),
        ),
        migrations.AlterField(
            model_name='bankquestion',
            name='choice_c',
            field=models.TextField(blank=True, verbose_name='C variant'),
        ),
        migrations.AlterField(
            model_name='bankquestion',
            name='choice_d',
            field=models.TextField(blank=True, verbose_name='D variant'),
        ),
        migrations.AlterField(
            model_name='bankquestion',
            name='correct_answer',
            field=models.CharField(max_length=50, verbose_name="To'g'ri javob"),
        ),
        migrations.AddIndex(
            model_name='bankquestion',
            index=models.Index(fields=['subject', 'category'], name='tests_app_b_subject_cat_idx'),
        ),
        migrations.CreateModel(
            name='SavedBankQuestion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('user_answer', models.CharField(blank=True, max_length=50)),
                ('is_correct', models.BooleanField(default=False)),
                ('saved_at', models.DateTimeField(auto_now_add=True)),
                ('question', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='saved_by_users', to='tests_app.bankquestion')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='saved_bank_questions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Saved Bank Question',
                'verbose_name_plural': 'Saved Bank Questions',
                'ordering': ['-saved_at'],
                'unique_together': {('user', 'question')},
            },
        ),
    ]
