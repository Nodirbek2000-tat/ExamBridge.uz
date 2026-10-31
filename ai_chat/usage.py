"""
What every AI call costs us, written down as it happens.

record() / record_chat() run next to each OpenAI call and never let a failure
reach the learner. OpenAI does not return a price, so cost_usd is worked out
here: update the tables when OpenAI changes them or a model is swapped. Old
rows keep the price they were saved with.
"""
import logging
from decimal import Decimal

from .models import AIUsage

logger = logging.getLogger(__name__)

MILLION = 1_000_000

# $ per 1M tokens: input, cached input and output are billed apart.
CHAT_PRICES = {
    'gpt-4o': {'input': 2.50, 'cached': 1.25, 'output': 10.00},
    'gpt-4.1': {'input': 2.00, 'cached': 0.50, 'output': 8.00},
}
WHISPER_PER_MINUTE = {'whisper-1': 0.006}
# $ per 1M characters. gpt-4o-mini-tts really bills audio tokens, which the speech
# API does not report: about $0.015 a minute, at roughly 900 characters a minute.
TTS_PER_MILLION_CHARS = {'tts-1-hd': 30.00, 'gpt-4o-mini-tts': 16.67}

# feature → (dashboard group, what it is)
FEATURES = {
    'ai_tutor': ('AI Tutor', 'AI Tutor chat'),
    'analysis_sat': ('SAT', 'AI analysis'),
    'analysis_ielts': ('IELTS', 'AI analysis'),
    'analysis_cefr': ('CEFR', 'AI analysis'),
    'ielts_speaking_grade': ('IELTS', 'Speaking grading'),
    'ielts_speaking_stt': ('IELTS', 'Speaking transcription'),
    'ielts_writing_grade': ('IELTS', 'Writing grading'),
    'ielts_tts': ('IELTS', 'Examiner voice'),
    'cefr_speaking_grade': ('CEFR', 'Speaking grading'),
    'cefr_speaking_stt': ('CEFR', 'Speaking transcription'),
    'cefr_writing_grade': ('CEFR', 'Writing grading'),
    'cefr_tts': ('CEFR', 'Examiner voice'),
    'games_stt': ('Games', 'Voice game transcription'),
    'games_tts': ('Games', 'Character voices'),
    'speaking_game_stt': ('Games', 'Speaking game transcription'),
    'shadowing_stt': ('Study Tools', 'Shadowing transcription'),
    'podcast_stt': ('Study Tools', 'Podcast transcription'),
}


def cost_of(model, input_tokens=0, cached_tokens=0, output_tokens=0, seconds=0, characters=0):
    """$ for one call. A model missing from the tables costs 0 and logs a warning."""
    if model in CHAT_PRICES:
        price = CHAT_PRICES[model]
        fresh = max(0, input_tokens - cached_tokens)
        total = (fresh * price['input'] + cached_tokens * price['cached']
                 + output_tokens * price['output']) / MILLION
    elif model in WHISPER_PER_MINUTE:
        total = seconds / 60 * WHISPER_PER_MINUTE[model]
    elif model in TTS_PER_MILLION_CHARS:
        total = characters * TTS_PER_MILLION_CHARS[model] / MILLION
    else:
        logger.warning('No AI price for model %s', model)
        total = 0
    return Decimal(str(round(total, 6)))


def record(feature, model, input_tokens=0, cached_tokens=0, output_tokens=0, seconds=0, characters=0):
    """Save one call. A failure here is logged, never raised."""
    try:
        AIUsage.objects.create(
            feature=feature, model=model,
            input_tokens=input_tokens, cached_tokens=cached_tokens, output_tokens=output_tokens,
            audio_seconds=seconds, characters=characters,
            cost_usd=cost_of(model, input_tokens, cached_tokens, output_tokens, seconds, characters),
        )
    except Exception:
        logger.exception('Could not record AI usage for %s', feature)


def record_chat(feature, model, usage):
    """Save a chat completion from its `usage` block: the SDK object or the raw JSON dict."""
    try:
        if not usage:
            return
        if hasattr(usage, 'model_dump'):
            usage = usage.model_dump()
        details = usage.get('prompt_tokens_details') or {}
        record(
            feature, model,
            input_tokens=usage.get('prompt_tokens') or 0,
            cached_tokens=details.get('cached_tokens') or 0,
            output_tokens=usage.get('completion_tokens') or 0,
        )
    except Exception:
        logger.exception('Could not read AI usage for %s', feature)
