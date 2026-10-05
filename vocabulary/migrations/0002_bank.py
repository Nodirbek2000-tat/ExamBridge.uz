# The shared word bank for Word Battle and Toby Run (RUNNER_PLAN §B5): Word gets the bank
# fields, Phrase and Review are new. All additive; the legacy UserWord is untouched.

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

LEVELS = [('A1', 'A1'), ('A2', 'A2'), ('B1', 'B1'), ('B2', 'B2'), ('C1', 'C1')]
STATUSES = [('draft', 'Qoralama'), ('reviewed', 'Tekshirilgan'), ('published', 'Chop etilgan')]


class Migration(migrations.Migration):

    dependencies = [
        ('vocabulary', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='word',
            name='definition',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='word',
            name='level',
            field=models.CharField(blank=True, choices=LEVELS, db_index=True, max_length=2),
        ),
        migrations.AddField(
            model_name='word',
            name='uz',
            field=models.CharField(blank=True, help_text='Main Uzbek meaning', max_length=120),
        ),
        migrations.AddField(
            model_name='word',
            name='pos',
            field=models.CharField(blank=True, choices=[('noun', 'noun'), ('verb', 'verb'), ('adj', 'adjective'), ('adv', 'adverb'), ('phrase', 'phrase'), ('other', 'other')], max_length=10),
        ),
        migrations.AddField(
            model_name='word',
            name='topic',
            field=models.CharField(blank=True, db_index=True, help_text='Slug from vocabulary.bank.TOPICS', max_length=30),
        ),
        migrations.AddField(
            model_name='word',
            name='picture',
            field=models.CharField(blank=True, help_text='ItemArt key (Toby’s Day pictures)', max_length=40),
        ),
        migrations.AddField(
            model_name='word',
            name='say_also',
            field=models.JSONField(blank=True, default=list, help_text='Accepted spoken forms (≤ 6)'),
        ),
        migrations.AddField(
            model_name='word',
            name='synonyms',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='word',
            name='antonyms',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='word',
            name='distractors',
            field=models.JSONField(blank=True, default=list, help_text='Word Battle wrong options (≤ 6)'),
        ),
        migrations.AddField(
            model_name='word',
            name='tags',
            field=models.JSONField(blank=True, default=list, help_text='e.g. ["sat"], ["kids"]'),
        ),
        migrations.AddField(
            model_name='word',
            name='speak',
            field=models.BooleanField(default=True, help_text='May be a single-word speaking target'),
        ),
        migrations.AddField(
            model_name='word',
            name='speak_risk',
            field=models.BooleanField(default=False, help_text='Set by the speakability check'),
        ),
        migrations.AddField(
            model_name='word',
            name='status',
            field=models.CharField(choices=STATUSES, db_index=True, default='published', max_length=10),
        ),
        migrations.AddField(
            model_name='word',
            name='source',
            field=models.CharField(blank=True, help_text='Import label', max_length=40),
        ),
        migrations.AddField(
            model_name='word',
            name='say_seen',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='word',
            name='say_ok',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='word',
            name='mean_seen',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='word',
            name='mean_ok',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='word',
            name='say_heard',
            field=models.JSONField(blank=True, default=list, help_text='Top mis-hears (nightly)'),
        ),
        migrations.AddField(
            model_name='word',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddIndex(
            model_name='word',
            index=models.Index(fields=['status', 'level', 'topic'], name='vocab_word_status_lvl_topic'),
        ),
        migrations.CreateModel(
            name='Phrase',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('echo', 'echo'), ('answer', 'answer'), ('fill', 'fill'), ('twister', 'twister')], max_length=10)),
                ('text', models.CharField(blank=True, help_text='echo / twister: the line · fill: the full sentence · answer: the model answer', max_length=200)),
                ('prompt', models.CharField(blank=True, help_text='answer: the question · fill: the sentence with ___', max_length=200)),
                ('answer', models.CharField(blank=True, help_text='fill: the missing chunk', max_length=80)),
                ('accept', models.JSONField(blank=True, default=list, help_text='answer: keyword groups [[..], [..]] · fill: extra accepted chunks')),
                ('min_words', models.PositiveSmallIntegerField(default=0)),
                ('uz', models.CharField(blank=True, max_length=240)),
                ('level', models.CharField(blank=True, choices=LEVELS, db_index=True, max_length=2)),
                ('topic', models.CharField(blank=True, db_index=True, max_length=30)),
                ('picture', models.CharField(blank=True, max_length=40)),
                ('status', models.CharField(choices=STATUSES, db_index=True, default='published', max_length=10)),
                ('source', models.CharField(blank=True, max_length=40)),
                ('voice', models.CharField(blank=True, choices=[('toby', 'toby'), ('girl', 'girl'), ('boy', 'boy'), ('mum', 'mum'), ('teacher', 'teacher'), ('man', 'man'), ('grandma', 'grandma'), ('driver', 'driver'), ('coach', 'coach'), ('narrator', 'narrator')], max_length=12)),
                ('key', models.CharField(help_text='kind|level|normalized prompt-or-text (upserts)', max_length=240, unique=True)),
                ('say_seen', models.PositiveIntegerField(default=0)),
                ('say_ok', models.PositiveIntegerField(default=0)),
                ('say_heard', models.JSONField(blank=True, default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['level', 'kind', 'id'],
                'indexes': [models.Index(fields=['status', 'level', 'topic', 'kind'], name='vocab_phrase_stat_lvl_tp_kind')],
            },
        ),
        migrations.CreateModel(
            name='Review',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('w', 'word'), ('p', 'phrase')], max_length=1)),
                ('item_id', models.PositiveIntegerField()),
                ('skill', models.CharField(choices=[('say', 'say'), ('mean', 'mean')], max_length=4)),
                ('box', models.PositiveSmallIntegerField(default=0)),
                ('due_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('seen', models.PositiveIntegerField(default=0)),
                ('ok', models.PositiveIntegerField(default=0)),
                ('miss', models.PositiveIntegerField(default=0)),
                ('first_ok', models.PositiveIntegerField(default=0)),
                ('best_ms', models.PositiveIntegerField(blank=True, null=True)),
                ('last_heard', models.CharField(blank=True, max_length=80)),
                ('last_verdict', models.CharField(blank=True, max_length=5)),
                ('promoted_on', models.DateField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='bank_reviews', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('user', 'kind', 'item_id', 'skill'), name='uniq_review_user_item_skill')],
                'indexes': [models.Index(fields=['user', 'skill', 'due_at'], name='review_user_skill_due_idx')],
            },
        ),
    ]
