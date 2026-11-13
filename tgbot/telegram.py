"""
The few Telegram Bot API calls the site makes itself: posting a receipt to
the payments channel, marking it decided, and telling the student.

Every call is best effort. Telegram being slow or down must never break a
payment, so failures are logged and None is returned.
"""
import json
import logging
import mimetypes

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT = 10
CAPTION_LIMIT = 1024


def _call(method, data, files=None):
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        return None
    try:
        res = requests.post(
            f'https://api.telegram.org/bot{token}/{method}',
            data=data, files=files, timeout=TIMEOUT,
        )
        body = res.json()
    except Exception:
        logger.exception('Telegram %s failed', method)
        return None
    if not body.get('ok'):
        logger.warning('Telegram %s refused: %s', method, body.get('description'))
        return None
    return body.get('result')


def _markup(buttons):
    """buttons: rows of {'text', 'callback_data' | 'url'} → reply_markup JSON."""
    return json.dumps({'inline_keyboard': buttons or []})


def send_message(chat_id, text, buttons=None):
    data = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': 'true'}
    if buttons:
        data['reply_markup'] = _markup(buttons)
    return _call('sendMessage', data)


def send_file(chat_id, file_field, caption, buttons=None):
    """Post a receipt. Photos go as photos; PDFs, and photos Telegram refuses, as files."""
    name = file_field.name.rsplit('/', 1)[-1]
    content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    data = {'chat_id': chat_id, 'caption': caption[:CAPTION_LIMIT], 'parse_mode': 'HTML'}
    if buttons:
        data['reply_markup'] = _markup(buttons)

    methods = [('sendPhoto', 'photo'), ('sendDocument', 'document')]
    if not content_type.startswith('image/'):
        methods = methods[1:]
    for method, field in methods:
        file_field.open('rb')
        try:
            file_field.seek(0)
            result = _call(method, data, files={field: (name, file_field.read(), content_type)})
        finally:
            file_field.close()
        if result:
            return result
    return None


def edit_caption(chat_id, message_id, caption, buttons=None):
    data = {
        'chat_id': chat_id,
        'message_id': message_id,
        'caption': caption[:CAPTION_LIMIT],
        'parse_mode': 'HTML',
        'reply_markup': _markup(buttons),   # an empty keyboard removes the buttons
    }
    return _call('editMessageCaption', data)
