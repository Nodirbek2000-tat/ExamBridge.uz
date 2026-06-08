from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('centers', '0002_centermembership_member_password'),
    ]

    operations = [
        migrations.AddField(
            model_name='assignment',
            name='task_ref_kind',
            field=models.CharField(blank=True, max_length=20),
        ),
    ]
