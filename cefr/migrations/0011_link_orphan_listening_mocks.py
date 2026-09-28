"""
Listening mocks imported before 2026-09-28 created their parts without a
CEFRTest, so admin and learners saw six loose parts. Link each such import
back into one test.

A group = consecutive orphan mock parts (by id) whose part numbers keep rising
and that were created within a minute of each other. Single parts are left
alone; nothing is deleted. Reverse is a no-op.
"""
import re

from django.db import migrations

PART_SUFFIX = re.compile(r'\s*[-–—:]\s*part\s*\d+\s*$', re.I)


def group_orphans(sections):
    groups, cur = [], []
    for s in sections:
        prev = cur[-1] if cur else None
        new_import = (
            prev is None
            or s.section_number <= prev.section_number
            or (s.created_at - prev.created_at).total_seconds() > 60
        )
        if new_import and cur:
            groups.append(cur)
            cur = []
        cur.append(s)
    if cur:
        groups.append(cur)
    return [g for g in groups if len(g) >= 2]


def link_orphans(apps, schema_editor):
    Section = apps.get_model('cefr', 'CEFRListeningSection')
    Test = apps.get_model('cefr', 'CEFRTest')
    orphans = list(Section.objects.filter(test__isnull=True, is_mock=True).order_by('id'))
    for group in group_orphans(orphans):
        first = group[0]
        title = PART_SUFFIX.sub('', first.title or '').strip() or 'CEFR Listening Mock'
        test = Test.objects.create(
            title=title,
            level=first.level or 'B2',
            test_type='LISTENING',
            time_limit=first.time_limit or 40,
            is_premium=first.is_premium,
            is_active=True,
        )
        Section.objects.filter(id__in=[s.id for s in group]).update(test=test, is_standalone=False)


class Migration(migrations.Migration):

    dependencies = [
        ('cefr', '0010_alter_cefrlisteningquestion_question_type'),
    ]

    operations = [
        migrations.RunPython(link_orphans, migrations.RunPython.noop),
    ]
