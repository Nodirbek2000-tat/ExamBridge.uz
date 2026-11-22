"""
The bot's morning message by hand.

  python manage.py daily_message                     # what today's message will be
  python manage.py daily_message --date 2026-11-26   # ... or another day's
  python manage.py daily_message --to 123456789      # send it to one Telegram chat (not recorded)
  python manage.py daily_message --send              # send today's to everyone now (once a day)
"""
from datetime import date

import requests
from django.core.management.base import BaseCommand, CommandError

from tgbot import daily


class Command(BaseCommand):
    help = "Show or send the bot's daily morning message."

    def add_arguments(self, parser):
        parser.add_argument('--date', help='YYYY-MM-DD (default: today)')
        parser.add_argument('--to', type=int, help='Send it to this one Telegram chat id only')
        parser.add_argument('--send', action='store_true', help="Send today's message to everyone now")

    def handle(self, *args, **opts):
        if opts['send']:
            post = daily.send_today()
            if post is None:
                raise CommandError("Not sent: today's message was already sent, or TELEGRAM_BOT_TOKEN is not set.")
            self.stdout.write(f'Sent {post.key}: {post.sent} delivered, {post.gone} gone, '
                              f'{post.failed} failed, of {post.total}.')
            return

        day = date.fromisoformat(opts['date']) if opts['date'] else daily.timezone.localdate()
        message = daily.pick(day)
        self.stdout.write(f"{day} · {message['key']}\n\n{message['text']}\n\n[{message['button']}] → {message['url']}")
        if opts['to']:
            with requests.Session() as session:
                result = daily.send_one(session, opts['to'],
                                        daily.payload(message['text'], message['button'], message['url']))
            self.stdout.write(f'\n→ {opts["to"]}: {result}')
