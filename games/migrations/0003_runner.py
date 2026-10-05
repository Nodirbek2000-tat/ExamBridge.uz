# Toby Run (RUNNER_PLAN §B8.1): the 'runner' slug for voice-game progress and runs, plus the
# run's idempotency `ref`, the leaderboard `ranked` flag and free-form `meta`. All additive.

from django.conf import settings
from django.db import migrations, models

VOICE_GAMES = [('tobys-day', "Toby's Day"), ('voice-drive', 'Voice Drive'), ('runner', 'Toby Run')]


class Migration(migrations.Migration):

    dependencies = [
        ('games', '0002_voicegameprogress_voicegamerun'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='voicegameprogress',
            name='slug',
            field=models.CharField(choices=VOICE_GAMES, max_length=40),
        ),
        migrations.AlterField(
            model_name='voicegamerun',
            name='slug',
            field=models.CharField(choices=VOICE_GAMES, max_length=40),
        ),
        migrations.AddField(
            model_name='voicegamerun',
            name='ref',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='voicegamerun',
            name='ranked',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='voicegamerun',
            name='meta',
            field=models.JSONField(blank=True, default=dict, help_text='Per-game details (mode, stt, flags…)'),
        ),
        migrations.AddConstraint(
            model_name='voicegamerun',
            constraint=models.UniqueConstraint(condition=models.Q(('ref', ''), _negated=True), fields=('user', 'ref'), name='uniq_voice_run_user_ref'),
        ),
    ]
