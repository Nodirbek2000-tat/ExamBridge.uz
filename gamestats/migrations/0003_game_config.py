# Game.config: the admin's overrides of a game's tuning (validated by gamestats/configs.py).
# Also renames the runner card RUNNER → TOBY RUN, only if an admin has not renamed it already.

from django.db import migrations, models


def rename_runner(apps, schema_editor):
    apps.get_model('gamestats', 'Game').objects.filter(slug='runner', title='RUNNER').update(title='TOBY RUN')


def unrename_runner(apps, schema_editor):
    apps.get_model('gamestats', 'Game').objects.filter(slug='runner', title='TOBY RUN').update(title='RUNNER')


class Migration(migrations.Migration):

    dependencies = [
        ('gamestats', '0002_seed_games'),
    ]

    operations = [
        migrations.AddField(
            model_name='game',
            name='config',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(rename_runner, unrename_runner),
    ]
