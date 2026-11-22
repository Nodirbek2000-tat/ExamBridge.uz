from celery import shared_task

from . import daily


@shared_task(ignore_result=True)
def send_daily_message():
    """08:00 (Tashkent): the bot's morning message to every student. Sent once a day."""
    daily.send_today()
