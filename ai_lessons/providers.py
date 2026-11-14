"""
Who writes and voices a lesson.

The pipeline only knows two calls, complete() and speak(), so the AI or the voice
can be swapped without touching it. settings.AI_LESSON_PROVIDER picks one:

    openai  the real thing; costs money and is recorded in the AI cost statistics
    fake    canned Uzbek content and silent audio, for building and testing without
            tokens. Its lessons are marked and can never be published.
"""
import io
import json
import logging
import re
from xml.sax.saxutils import escape as xml_escape

from django.conf import settings

from ai_chat.usage import record, record_chat

logger = logging.getLogger(__name__)

WORDS_PER_SECOND = 2.4

# One silent MPEG-1 Layer III frame: 32 kbps, 44.1 kHz, mono.
SILENT_FRAME = b'\xff\xfb\x10\xc4' + bytes(100)
FRAME_SECONDS = 1152 / 44100


class LessonAIError(Exception):
    """A failure the admin should read. The message goes to the panel as it is."""


class TemporaryAIError(LessonAIError):
    """Worth trying again in a minute: a timeout, a rate limit, a server hiccup."""


class OutOfCredit(LessonAIError):
    """The account has no money left. The job can be continued after a top-up."""


class BadReply(LessonAIError):
    """The reply is not usable. The pipeline asks once more, saying what was wrong."""


def _spoken_seconds(text):
    return max(1.0, len(text.split()) / WORDS_PER_SECOND)


# ── What the voice engine is given ────────────────────────────────────────────

_MARKUP = [
    (re.compile(r'\\[()\[\]]'), ' '),          # \( \) \[ \] around inline maths
    (re.compile(r'\*\*|__|`|#+\s'), ''),          # markdown emphasis and headings
    (re.compile(r'\s*[→⇒➜]\s*'), ', '),            # arrows read as a pause
    (re.compile(r'[•▪●]'), ''),
    (re.compile(r'\s{2,}'), ' '),
]


def speakable(text):
    """The spoken text without the board's markup, which a voice would read out as symbols."""
    for pattern, replacement in _MARKUP:
        text = pattern.sub(replacement, text)
    return text.strip()


# How the teacher should sound (gpt-4o-mini-tts follows these instructions).
VOICE_INSTRUCTIONS = {
    'uz': (
        "You are a warm, confident teacher from Tashkent speaking Uzbek (Latin script) to students. "
        "Pronounce every word with natural native Uzbek pronunciation and stress: o' and g' as in Uzbek, "
        "q as a deep q, x as kh, h softly. Do not sound like a foreigner reading Uzbek. "
        "English terms inside the text are said with clear English pronunciation. "
        "Calm, steady classroom pace with short natural pauses at commas and full stops; friendly, never theatrical."
    ),
    'en': (
        "You are a warm, confident teacher explaining to exam students. Clear, natural pronunciation, "
        "calm classroom pace with short pauses between ideas; friendly, never theatrical."
    ),
}

# Azure's native Uzbek voices; the lesson's voice picks the gender.
AZURE_VOICES = {'female': 'uz-UZ-MadinaNeural', 'male': 'uz-UZ-SardorNeural'}
MALE_VOICES = {'onyx', 'echo', 'ash'}


class FakeProvider:
    """Answers instantly from `context`, so every path can run without spending anything."""

    name = 'fake'

    def complete(self, task, system, user, context, max_tokens, model=None):
        return getattr(self, f'_{task}')(context)

    def speak(self, text, voice, language='uz'):
        frames = round(_spoken_seconds(text) / FRAME_SECONDS)
        return SILENT_FRAME * frames, frames * FRAME_SECONDS

    @staticmethod
    def _mark(context):
        words = len(context['text'].split())
        verdict = 'CORRECT' if words >= 5 else 'PARTLY' if words >= 2 else 'WRONG'
        return {'verdict': verdict, 'feedback': "Soxta baho: javob so'zlari soniga qarab qo'yildi."}

    @staticmethod
    def _plan(context):
        count = context['sections']
        return {'sections': [
            {'title': f"{i + 1}-bo'lim", 'goal': f"{context['title']}: {i + 1}-qadam", 'minutes': 2}
            for i in range(count)
        ]}

    @staticmethod
    def _section(context):
        title = context['section']['title']
        return {'steps': [
            {
                'say': f"Bu {title}. Bu matn sinov uchun yozilgan, haqiqiy AI hali ulanmagan. "
                       f"Bu yerda ustoz mavzuni tushuntiradi.",
                'board': [
                    {'type': 'heading', 'text': title, 'at': 0},
                    {'type': 'text', 'text': 'Sinov matni: **muhim** fikr', 'at': 0.3},
                    {'type': 'math', 'tex': 'a^2 + b^2 = c^2', 'at': 0.6},
                ],
            },
            {
                'say': "Endi shu fikrni qisqacha takrorlaymiz. Keyingi qismga o'tamiz.",
                'board': [
                    {'type': 'list', 'items': ['Birinchi fikr', 'Ikkinchi fikr'], 'at': 0},
                    {'type': 'note', 'text': 'Sinov eslatmasi', 'at': 0.6},
                ],
            },
        ]}

    @staticmethod
    def _questions(context):
        choice = {
            'kind': 'CHOICE', 'prompt': 'Sinov savoli: 2 + 2 nechaga teng?', 'options': ['3', '4', '5'],
            'correct': 1, 'explanation': '2 va 2 ni qo\'shsak 4 bo\'ladi.',
        }
        written = {
            'kind': 'WRITTEN', 'prompt': 'Sinov savoli: mavzuni bir gapda tushuntiring.',
            'rubric': 'Asosiy fikr aytilgan bo\'lishi kerak.', 'explanation': 'Namuna javob.',
        }
        return {'questions': [
            {**choice, 'after_step': 0},
            {**written, 'after_step': min(1, context['steps'] - 1)},
            {**choice, 'is_homework': True, 'prompt': 'Uy vazifasi: 3 + 3 nechaga teng?',
             'options': ['5', '6'], 'correct': 1},
            {**written, 'is_homework': True},
        ]}

    @staticmethod
    def _review(context):
        return {
            'verdict': 'GOOD',
            'summary': 'Soxta AI tekshiruvi: dars tuzilishi to\'g\'ri, mazmuni sinov uchun.',
            'issues': [],
            'questions': [
                {'index': i, 'verdict': 'GOOD', 'note': 'Soxta tekshiruv.'}
                for i in range(context['questions'])
            ],
        }


class OpenAIProvider:
    name = 'openai'
    CHAT_TIMEOUT = 180
    VOICE_TIMEOUT = 120
    AZURE_TIMEOUT = 60

    def __init__(self):
        if not settings.OPENAI_API_KEY:
            raise LessonAIError('OpenAI API key is not configured.')
        self.model = settings.AI_LESSON_MODEL

    def _client(self, timeout):
        from openai import OpenAI
        return OpenAI(api_key=settings.OPENAI_API_KEY, timeout=timeout, max_retries=0)

    @staticmethod
    def _translate(error):
        """An OpenAI SDK error → what the pipeline should do about it."""
        import openai
        if isinstance(error, openai.RateLimitError):
            if getattr(error, 'code', None) == 'insufficient_quota':
                return OutOfCredit('OpenAI balance is empty. Top it up, then continue this job.')
            return TemporaryAIError('OpenAI rate limit reached. Trying again shortly.')
        if isinstance(error, (openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError)):
            return TemporaryAIError(f'OpenAI is not responding: {error.__class__.__name__}.')
        if isinstance(error, openai.AuthenticationError):
            return LessonAIError('OpenAI rejected the API key.')
        return LessonAIError(f'OpenAI error: {error.__class__.__name__}.')

    def complete(self, task, system, user, context, max_tokens, model=None):
        import openai
        model = model or self.model
        try:
            reply = self._client(self.CHAT_TIMEOUT).chat.completions.create(
                model=model, temperature=0.3, max_tokens=max_tokens,
                response_format={'type': 'json_object'},
                messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            )
        except openai.OpenAIError as error:
            raise self._translate(error) from error

        record_chat(f'lesson_{task}', model, reply.usage)
        choice = reply.choices[0]
        if choice.finish_reason == 'length':
            raise BadReply('The reply was cut off. Write less.')
        try:
            data = json.loads(choice.message.content or '')
        except ValueError as error:
            raise BadReply('The reply was not valid JSON.') from error
        if not isinstance(data, dict):
            raise BadReply('The reply must be a JSON object.')
        return data

    def speak(self, text, voice, language='uz'):
        text = speakable(text)
        if settings.AI_LESSON_TTS_ENGINE == 'azure' and language == 'uz':
            return self._speak_azure(text, voice)
        return self._speak_openai(text, voice, language)

    def _speak_openai(self, text, voice, language, model=None):
        import openai
        model = model or settings.AI_LESSON_TTS_MODEL
        extra = {}
        if model.startswith('gpt-'):          # tts-1 models do not take instructions
            extra['instructions'] = VOICE_INSTRUCTIONS.get(language, VOICE_INSTRUCTIONS['en'])
        try:
            reply = self._client(self.VOICE_TIMEOUT).audio.speech.create(
                model=model, voice=voice, input=text, response_format='mp3', **extra,
            )
        except openai.OpenAIError as error:
            raise self._translate(error) from error
        data = reply.content
        record('lesson_voice', model, characters=len(text))
        return data, mp3_seconds(data, text)

    def _speak_azure(self, text, voice, rate='-4%'):
        """Native Uzbek voice from Azure Speech (REST, SSML)."""
        import requests
        if not settings.AZURE_SPEECH_KEY:
            raise LessonAIError('AZURE_SPEECH_KEY is not set, so the Azure Uzbek voice cannot be used.')
        name = AZURE_VOICES['male' if voice in MALE_VOICES else 'female']
        ssml = (
            "<speak version='1.0' xml:lang='uz-UZ'>"
            f"<voice name='{name}'><prosody rate='{rate}'>{xml_escape(text)}</prosody></voice></speak>"
        )
        try:
            res = requests.post(
                f'https://{settings.AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1',
                data=ssml.encode('utf-8'), timeout=self.AZURE_TIMEOUT,
                headers={
                    'Ocp-Apim-Subscription-Key': settings.AZURE_SPEECH_KEY,
                    'Content-Type': 'application/ssml+xml',
                    'X-Microsoft-OutputFormat': 'audio-24khz-96kbitrate-mono-mp3',
                    'User-Agent': 'ExamBridge',
                },
            )
        except requests.RequestException as error:
            raise TemporaryAIError(f'Azure Speech is not responding: {error.__class__.__name__}.') from error
        if res.status_code == 429 or res.status_code >= 500:
            raise TemporaryAIError(f'Azure Speech is busy ({res.status_code}). Trying again shortly.')
        if res.status_code != 200:
            raise LessonAIError(f'Azure Speech refused the request ({res.status_code}). Check the key and region.')
        record('lesson_voice', 'azure-neural-tts', characters=len(text))
        return res.content, mp3_seconds(res.content, text)


def mp3_seconds(data, text):
    """How long an MP3 plays. The estimate only stands in if the file cannot be read."""
    from mutagen.mp3 import HeaderNotFoundError, MP3
    try:
        return MP3(io.BytesIO(data)).info.length
    except (HeaderNotFoundError, ValueError):
        logger.warning('Could not read the length of a lesson recording')
        return _spoken_seconds(text)


def get_provider():
    if settings.AI_LESSON_PROVIDER == FakeProvider.name:
        return FakeProvider()
    return OpenAIProvider()
