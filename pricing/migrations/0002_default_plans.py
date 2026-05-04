from django.db import migrations


def create_default_plans(apps, schema_editor):
    Plan = apps.get_model('pricing', 'Plan')
    Plan.objects.get_or_create(
        plan_type='FREE',
        defaults={
            'name': 'Free',
            'price': 0.00,
            'duration_days': 32767,
            'features': [
                '3 full tests per month',
                'Score report (400–1600)',
                'Answer review',
                'Save questions',
            ],
            'is_active': True,
            'order': 1,
        }
    )
    Plan.objects.get_or_create(
        plan_type='PRO',
        defaults={
            'name': 'Pro',
            'price': 12.00,
            'duration_days': 30,
            'features': [
                'Unlimited tests',
                'Score report & review',
                'AI score analysis',
                'University finder',
                'Full test history',
                'Saved questions',
                'Vocabulary builder',
            ],
            'is_active': True,
            'order': 2,
        }
    )
    Plan.objects.get_or_create(
        plan_type='PREMIUM',
        defaults={
            'name': 'Annual',
            'price': 8.00,
            'duration_days': 365,
            'features': [
                'Everything in Pro',
                'Priority support',
                'Early test access',
                'Vocabulary builder',
                'Courses access',
            ],
            'is_active': True,
            'order': 3,
        }
    )


def remove_default_plans(apps, schema_editor):
    Plan = apps.get_model('pricing', 'Plan')
    Plan.objects.filter(plan_type__in=['FREE', 'PRO', 'PREMIUM']).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('pricing', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_default_plans, remove_default_plans),
    ]
