from django.db import migrations

GAMES = [
    # slug, title, status, order
    ('tobys-day', 'TOBY’S DAY', 'live', 10),
    ('voice-drive', 'VOICE DRIVE', 'live', 20),
    ('speaking', 'SPEAKING', 'live', 30),
    ('word-battle', 'WORD BATTLE', 'soon', 40),
    ('runner', 'RUNNER', 'soon', 50),
]


def seed(apps, schema_editor):
    Game = apps.get_model('gamestats', 'Game')
    for slug, title, status, order in GAMES:
        # never overwrite what an admin already chose
        Game.objects.get_or_create(slug=slug, defaults={'title': title, 'status': status, 'order': order})


def unseed(apps, schema_editor):
    apps.get_model('gamestats', 'Game').objects.filter(slug__in=[g[0] for g in GAMES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('gamestats', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
