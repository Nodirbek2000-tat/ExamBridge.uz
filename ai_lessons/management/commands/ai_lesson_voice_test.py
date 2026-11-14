"""
Listen before you choose: the same Uzbek teaching paragraph in every available voice.

    python manage.py ai_lesson_voice_test
    python manage.py ai_lesson_voice_test --voices nova,coral,ash --text "O'z matningiz"

Files go to MEDIA/voice_tests/<time>/ and the command prints their links.
Costs a few cents (about 600 characters per voice).
"""
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum
from django.utils import timezone

from ai_chat.models import AIUsage
from ai_lessons.providers import AZURE_VOICES, OpenAIProvider, LessonAIError

SAMPLE = (
    "Assalomu alaykum, aziz o'quvchilar! Bugun kvadrat tenglamalarni o'rganamiz. "
    "Masalan: iks kvadrat plyus uch iks minus to'rt teng nol. Bu yerda a birga, b uchga, c esa minus to'rtga teng. "
    "Diskriminantni topish uchun b ning kvadratidan to'rt a c ni ayiramiz: to'qqiz qo'shuv o'n olti, ya'ni yigirma besh. "
    "Esda tuting: SAT imtihonida, ayniqsa Math bo'limida, bunday savollar tez-tez uchraydi."
)


class Command(BaseCommand):
    help = 'Record one Uzbek paragraph in every available lesson voice, to compare them by ear.'

    def add_arguments(self, parser):
        parser.add_argument('--text', default=SAMPLE)
        parser.add_argument('--voices', default='nova,coral,sage,onyx,ash',
                            help='OpenAI voices for gpt-4o-mini-tts, comma separated')

    def handle(self, *args, **opts):
        try:
            provider = OpenAIProvider()
        except LessonAIError as error:
            raise CommandError(str(error))

        started = timezone.now()
        folder = f"voice_tests/{timezone.localtime():%Y%m%d-%H%M%S}"
        base = settings.FRONTEND_URL.rstrip('/') + settings.MEDIA_URL
        text = opts['text']
        voices = [v.strip() for v in opts['voices'].split(',') if v.strip()]

        jobs = [('openai-tts-1-hd-nova (eski ovoz)', lambda: provider._speak_openai(text, 'nova', 'uz', model='tts-1-hd'))]
        for voice in voices:
            jobs.append((f'openai-gpt-4o-mini-tts-{voice}',
                         lambda v=voice: provider._speak_openai(text, v, 'uz', model='gpt-4o-mini-tts')))
        if settings.AZURE_SPEECH_KEY:
            jobs.append(('azure-madina (ayol)', lambda: provider._speak_azure(text, 'nova')))
            jobs.append(('azure-sardor (erkak)', lambda: provider._speak_azure(text, 'onyx')))
        else:
            self.stdout.write(self.style.WARNING(
                "Azure kaliti yo'q (AZURE_SPEECH_KEY) — Azure o'zbek ovozlari (Madina, Sardor) o'tkazib yuborildi."))

        self.stdout.write(f'{len(jobs)} ta ovoz yozilmoqda...\n')
        for label, speak in jobs:
            try:
                audio, seconds = speak()
            except LessonAIError as error:
                self.stdout.write(self.style.ERROR(f'  ✗ {label}: {error}'))
                continue
            slug = label.split(' ')[0]
            path = default_storage.save(f'{folder}/{slug}.mp3', ContentFile(audio))
            self.stdout.write(f'  ✓ {label:<38} {seconds:5.1f} s   {base}{path}')

        spent = AIUsage.objects.filter(feature='lesson_voice', created_at__gte=started).aggregate(s=Sum('cost_usd'))['s'] or 0
        self.stdout.write(f'\nSarflandi: ${spent:.4f}')
        self.stdout.write("Havolalarni brauzerda ochib tinglang va eng tabiiy o'zbekcha ovozni tanlang.")
        self.stdout.write(f"Azure ovozlari: {', '.join(AZURE_VOICES.values())}")
