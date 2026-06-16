from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_user_stripe_customer_id_user_stripe_subscription_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='premium_source',
            field=models.CharField(blank=True, default='', help_text="'manual' (admin-granted) | 'stripe' (purchased)", max_length=20),
        ),
    ]
