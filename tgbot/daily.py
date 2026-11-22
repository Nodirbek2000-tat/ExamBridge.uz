"""
The bot's morning message: every day at 08:00 (Tashkent) every student gets one.

Each weekday has its own theme (Monday speaking, Tuesday writing, ...), and each theme
has several messages. A message is not repeated until the others of its theme have been
used, and the closing motivation line rotates the same way, so no two days look alike.
Under the message is one button that opens the right page of the site inside Telegram
(a Mini App); the site signs the student in by their Telegram (see site_views).

Every day is sent once: the DailyPost row for the date is made first, and a second run
for the same date finds it and stops.
"""
import logging
import random
import time

import requests
from django.conf import settings
from django.utils import timezone

from .models import DailyPost, TelegramAccount

logger = logging.getLogger(__name__)

SEND_GAP = 0.05           # 20 messages a second, well under Telegram's limit of 30
SAVE_EVERY = 100          # write the counts to the database every this many messages
TIMEOUT = 15

IELTS_SPEAKING = '/app/ielts/skills?tab=speaking'
IELTS_WRITING = '/app/ielts/skills?tab=writing'
IELTS_LISTENING = '/app/ielts/skills?tab=listening'
IELTS_READING = '/app/ielts/skills?tab=reading'
IELTS_MOCK = '/app/ielts/skills?tab=mock'
CEFR_SPEAKING = '/app/cefr/skills?tab=speaking'
CEFR_WRITING = '/app/cefr/skills?tab=writing'
CEFR_LISTENING = '/app/cefr/skills?tab=listening'
CEFR_MOCK = '/app/cefr/skills?tab=mock'

SPEAK = '🎙 Speaking qilish'
WRITE = '✍️ Writing yozish'
LISTEN = '🎧 Listening'
MOCK = '📝 Mock test'
PLAY = "🎮 O'ynash"
LESSON = "🧑‍🏫 Darsni ko'rish"

# Monday is 0. One theme a day.
WEEK = ['speaking', 'writing', 'listening', 'partner', 'learn', 'mock', 'easy']

# (text, button, page). In 'partner' messages {name} is filled in.
MESSAGES = {
    'speaking': [
        ("☀️ Xayrli tong! Bugun — <b>speaking kuni</b> 🎙\n"
         "IELTS Speaking Part 1 dan 3 ta savolga javob bering. 5 daqiqa yetadi, AI esa darhol band ko'rsatadi.",
         SPEAK, IELTS_SPEAKING),
        ("🎙 Bugun ozroq gapiramiz!\n"
         "Ovoz chiqarib gapirish — speaking'ni o'stirishning eng tez yo'li. Bitta Part 2 kartochkasini oling va 2 daqiqa gapiring.",
         SPEAK, IELTS_SPEAKING),
        ("🗣 <b>CEFR Speaking</b> vaqti!\n"
         "Bugun bitta speaking topshirig'ini bajaring: savolni eshiting, javob bering — AI xatolaringizni ko'rsatadi.",
         SPEAK, CEFR_SPEAKING),
        ("☕ Tongni speaking bilan boshlang!\n"
         "Bugun speaking o'yinida bitta matnni ovoz chiqarib o'qing — AI har bir so'zingizni tekshiradi.",
         PLAY, '/games/speaking'),
        ("🎙 Speaking'dan qo'rqmang!\n"
         "Xato qilish — o'rganishning bir qismi. Bugun 3 ta savolga javob bering, AI nimani yaxshilashni aytadi.",
         SPEAK, IELTS_SPEAKING),
    ],
    'writing': [
        ("✍️ Bugun — <b>writing kuni</b>!\n"
         "CEFR'dan bitta writing yozing. AI uni tekshirib, xatolaringizni ko'rsatadi.",
         WRITE, CEFR_WRITING),
        ("📝 IELTS Writing Task 2 — bugun bitta esse!\n"
         "40 daqiqa vaqt qo'ying va yozing. AI esseni band bilan baholaydi.",
         WRITE, IELTS_WRITING),
        ("✍️ 20 daqiqa — bitta Task 1.\n"
         "Grafikni tasvirlab yozing. Kichik qadam, lekin har kuni — katta natija.",
         WRITE, IELTS_WRITING),
        ("📚 Writing'ni o'stirishning siri — yaxshi namunalarni o'qish.\n"
         "Bugun bitta yuqori band'li namunani o'qing, keyin xuddi shunday o'zingiz yozing.",
         "📚 Namunalarni ko'rish", '/study/writing-samples'),
        ("✍️ CEFR Writing: bugun xat yozamiz!\n"
         "Do'stingizga yoki rasmiy xat — tanlang va yozing. AI grammatika va so'z boyligini tekshiradi.",
         WRITE, CEFR_WRITING),
    ],
    'listening': [
        ("🎧 Bugun — <b>listening kuni</b>!\n"
         "Bitta listening testini ishlang va xato qilgan savollaringizni qayta eshiting.",
         LISTEN, IELTS_LISTENING),
        ("🎧 Listening'dagi muammolarni tuzatamiz!\n"
         "Xato javoblaringizni ko'rib chiqing: qaysi so'zni eshitmadingiz? Bugun aynan shunga e'tibor bering.",
         LISTEN, IELTS_LISTENING),
        ("🎧 Quloqni mashq qildiramiz!\n"
         "Bitta CEFR listening topshirig'ini ishlang. Har kuni 20 daqiqa — bir oyda katta farq.",
         LISTEN, CEFR_LISTENING),
        ("🔁 Bugun shadowing qilib ko'ring!\n"
         "Gapni eshiting va xuddi shunday takrorlang — listening ham, talaffuz ham o'sadi.",
         '🔁 Shadowing', '/study/shadowing'),
        ("🎧 Listening Part 1 — eng oson ball!\n"
         "Ismlar, raqamlar, sanalar... Bugun shu qismni ishlang va bu ballarni boy bermang.",
         LISTEN, IELTS_LISTENING),
    ],
    'partner': [
        ("{name} bugun siz bilan <b>IELTS Speaking</b> qilmoqchi 🎙\n"
         "3 ta savol, 5 daqiqa. Tayyormisiz?",
         SPEAK, IELTS_SPEAKING),
        ("🎙 {name} sizni speaking'ga chaqiryapti!\n"
         "Part 1 savollari tayyor — bugun gaplashib olamiz.",
         SPEAK, IELTS_SPEAKING),
        ("{name} kutyapti 😊\n"
         "Bugun IELTS Speaking'da kim yaxshiroq gapirishini ko'ramiz!",
         SPEAK, IELTS_SPEAKING),
        ("🎙 {name}: «Bugun men bilan speaking qilasizmi?»\n"
         "Bir nechta savol — va band'ingiz tayyor.",
         SPEAK, IELTS_SPEAKING),
        ("☀️ Xayrli tong! {name} siz bilan speaking mashq qilmoqchi.\n"
         "Ovoz chiqarib gapiring — bugun 5 daqiqa yetadi.",
         SPEAK, IELTS_SPEAKING),
    ],
    'learn': [
        ("🧑‍🏫 Bugun Toby bilan dars!\n"
         "Qisqa AI video dars: Toby doskada tushuntiradi, siz savollarga javob berasiz.",
         LESSON, '/app/lessons'),
        ("📖 Bugun — <b>reading kuni</b>!\n"
         "Bitta passage o'qing. Avval savollarni ko'ring, keyin matndan javob qidiring.",
         '📖 Reading', IELTS_READING),
        ("🧠 Bugun yangi so'zlar!\n"
         "So'z boyligi — har bir bo'limda qo'shimcha ball. Lug'at bo'limida bugungi so'zlarni o'rganing.",
         "🧠 So'z o'rganish", '/app/vocabulary'),
        ("🧑‍🏫 Grammatikadagi xatolar uchun darslar bor!\n"
         "AI darslar bo'limidan o'zingizga mosini tanlang va 10 daqiqada tushunib oling.",
         LESSON, '/app/lessons'),
        ("📖 True / False / Not Given qiyinmi?\n"
         "Bugun bitta reading ishlang va shu savollarga alohida e'tibor bering.",
         '📖 Reading', IELTS_READING),
    ],
    'mock': [
        ("📝 Shanba — <b>mock test kuni</b>!\n"
         "Bugun to'liq IELTS mock ishlang va vaqtni xuddi imtihondagidek hisoblang.",
         MOCK, IELTS_MOCK),
        ("🏁 Haftani natija bilan yakunlaymiz!\n"
         "Bitta mock test — va qayerda turganingizni aniq bilasiz.",
         MOCK, IELTS_MOCK),
        ("📝 Imtihon oldidan eng yaxshi mashq — mock test.\n"
         "Bugun bir soat ajrating: telefonni chetga qo'ying va boshlang.",
         MOCK, IELTS_MOCK),
        ("📊 O'tgan haftadan yaxshiroqmisiz?\n"
         "CEFR mock testini ishlab, natijangizni solishtiring.",
         MOCK, CEFR_MOCK),
    ],
    'easy': [
        ("🎮 Yakshanba — o'ynab o'rganamiz!\n"
         "Speaking o'yinida matnni ovoz chiqarib o'qing — AI har bir so'zingizni baholaydi.",
         PLAY, '/games/speaking'),
        ("🚗 Voice Drive'da poyga!\n"
         "Ingliz tilida gapiring va mashinani boshqaring. O'yin — lekin talaffuz o'sadi.",
         PLAY, '/games/voice-drive'),
        ("🌤 Bugun yengil kun.\n"
         "10 daqiqa o'yin — va til mashqi ham bajarildi. Ertaga yangi kuch bilan!",
         PLAY, '/games'),
        ("🧠 Yakshanba — so'zlarni takrorlash kuni.\n"
         "Hafta davomida o'rgangan so'zlaringizni bir ko'rib chiqing.",
         "🧠 So'zlarni takrorlash", '/app/vocabulary'),
    ],
}

# The 'partner' day goes round these names, a new one each week.
NAMES = ['Moxichehra', 'Rano', 'Malika', 'Dilnoza', 'Sevara', 'Madina',
         'Zarina', 'Nilufar', 'Shahzoda', 'Gulnoza', 'Kamola', 'Mohira']

QUOTES = [
    "Har kuni ozgina — bir kun katta natija.",
    "Bugungi mashqingiz — ertangi band'ingiz.",
    "Xato qilmaydigan odam — hech narsa qilmaydigan odam.",
    "Boshlash uchun motivatsiya kerak, davom etish uchun — odat.",
    "Sabr qilgan maqsadiga yetadi.",
    "Imtihonni o'sha kun emas, har kungi mashq hal qiladi.",
    "Kichik qadam ham — oldinga qadam.",
    "Kuniga 20 daqiqa — oyiga 10 soat.",
    "Eng qiyini — boshlash. Qolgani osonroq.",
    "O'zingizni boshqalar bilan emas, kechagi o'zingiz bilan solishtiring.",
    "Yuqori band bir kunda kelmaydi, lekin har kuni yaqinlashadi.",
    "Til — mashq bilan ochiladigan eshik.",
    "Charchasangiz dam oling, lekin to'xtamang.",
    "Har bir to'g'ri javob — ishonchingizga yana bir g'isht.",
    "Siz o'ylagandan ko'ra kuchliroqsiz.",
    "Kecha o'tdi, ertaga hali kelmadi — bugun sizniki.",
    "Maqsad aniq bo'lsa, yo'l topiladi.",
    "Yaxshi natija tasodif emas, odat.",
    "Bugun boshlang — ertaga o'zingizga rahmat aytasiz.",
    "Mukammal bo'lish shart emas, har kuni bo'lish shart.",
]


def site_url():
    """Telegram opens only https pages in a Mini App."""
    url = (getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/')
    return url if url.startswith('https://') else 'https://exambridge.uz'


def _not_recent(count, used):
    """Indexes 0..count-1 not among `used`; all of them once every one was used."""
    left = [i for i in range(count) if i not in used]
    return left or list(range(count))


def pick(day):
    """The message for `day`: {'key', 'quote', 'text', 'button', 'url'}. The same day gives the same pick."""
    theme = WEEK[day.weekday()]
    options = MESSAGES[theme]
    rng = random.Random(day.toordinal())
    earlier = DailyPost.objects.filter(date__lt=day)

    recent_keys = earlier.filter(key__startswith=f'{theme}:').values_list('key', flat=True)[:len(options) - 1]
    used = {int(k.split(':')[1]) for k in recent_keys if k.split(':')[1].isdigit()}
    index = rng.choice(_not_recent(len(options), used))

    recent_quotes = set(earlier.values_list('quote', flat=True)[:len(QUOTES) - 1])
    quote = rng.choice(_not_recent(len(QUOTES), recent_quotes))

    text, button, path = options[index]
    if '{name}' in text:
        text = text.replace('{name}', NAMES[(day.toordinal() // 7) % len(NAMES)])
    return {
        'key': f'{theme}:{index}',
        'quote': quote,
        'text': f'{text}\n\n💬 <i>{QUOTES[quote]}</i>',
        'button': button,
        'url': site_url() + path,
    }


def payload(text, button, url):
    """The sendMessage body: the text and one button opening `url` inside Telegram."""
    return {
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
        'reply_markup': {'inline_keyboard': [[{'text': button, 'web_app': {'url': url}}]]},
    }


def send_one(session, chat_id, body):
    """'sent', 'gone' (blocked the bot, deleted their account, never started it) or 'failed'."""
    url = f'https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage'
    for _ in range(5):
        try:
            answer = session.post(url, json={**body, 'chat_id': chat_id}, timeout=TIMEOUT).json()
        except Exception:
            logger.exception('Daily message to %s failed', chat_id)
            return 'failed'
        if answer.get('ok'):
            return 'sent'
        code = answer.get('error_code')
        if code == 429:                               # Telegram asks to slow down
            time.sleep((answer.get('parameters') or {}).get('retry_after', 1) + 1)
            continue
        description = str(answer.get('description', '')).lower()
        if code == 403 or 'chat not found' in description:
            return 'gone'
        logger.warning('Daily message to %s refused: %s', chat_id, answer.get('description'))
        return 'failed'
    return 'failed'


def recipients():
    return list(TelegramAccount.objects.filter(user__is_active=True).order_by('id')
                .values_list('telegram_id', flat=True))


def send_today(day=None):
    """Send `day`'s message (today by default) to every student. Returns the DailyPost, or None if
    it was already sent that day or the bot has no token."""
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning('Daily message skipped: TELEGRAM_BOT_TOKEN is not set')
        return None
    day = day or timezone.localdate()
    if DailyPost.objects.filter(date=day).exists():
        return None
    post, created = DailyPost.objects.get_or_create(date=day, defaults=pick(day))
    if not created:                                   # another run made it a moment ago
        return None

    ids = recipients()
    post.total = len(ids)
    post.save(update_fields=['total'])
    body = payload(post.text, post.button, post.url)
    counts = {'sent': 0, 'gone': 0, 'failed': 0}
    with requests.Session() as session:
        for n, chat_id in enumerate(ids, 1):
            counts[send_one(session, chat_id, body)] += 1
            if n % SAVE_EVERY == 0:
                DailyPost.objects.filter(pk=post.pk).update(**counts)
            time.sleep(SEND_GAP)
    for field, value in counts.items():
        setattr(post, field, value)
    post.finished_at = timezone.now()
    post.save(update_fields=[*counts, 'finished_at'])
    logger.info('Daily message %s: %s of %s sent', post.key, post.sent, post.total)
    return post
