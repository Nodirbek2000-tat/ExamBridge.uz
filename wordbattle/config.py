"""
Word Battle tuning the admin can change without a deploy.

gamestats.configs loads this module for the slug 'word-battle' (its PLUGINS table), so
PATCH /api/games/stats/admin/games/word-battle/ {config: {...}} is validated here and
game_config('word-battle') returns DEFAULTS merged with the admin's overrides.
"""
from gamestats.configs import ConfigError, _flag, _number

from .models import LEVELS, QTYPES

DEFAULTS = {
    'question_ms': 7000,          # time per question
    'grace_ms': 1500,             # network allowance after the visible timer ends
    'questions': 15,              # per round
    'levels': list(LEVELS),       # A2 B1 B2 C1 SAT
    'types': list(QTYPES),        # en_uz uz_en syn ant cloze listen
    'ghost_share': 0.6,           # chance to meet a recorded real player when one exists
    'duel_hours': 48,             # how long a duel link stays open
    'leaderboard': True,          # weekly board on the home screen
}

_RANGES = {
    'question_ms': (4000, 15000, True),
    'grace_ms': (500, 3000, True),
    'questions': (10, 20, True),
    'ghost_share': (0, 1, False),
    'duel_hours': (6, 168, True),
}


def _subset(key, value, allowed):
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        raise ConfigError(f'{key}: ro‘yxat bo‘lishi kerak, masalan {list(allowed)[:2]}')
    if any(v not in allowed for v in value) or len(set(value)) != len(value):
        raise ConfigError(f'{key}: faqat {", ".join(allowed)} (takrorsiz)')
    return [v for v in allowed if v in value]


def validate_config(patch):
    """{key: value} → clean {key: value}. None = back to the default. Raises ConfigError."""
    if not isinstance(patch, dict):
        raise ConfigError('config obyekt bo‘lishi kerak')
    unknown = sorted(str(k) for k in patch if k not in DEFAULTS)
    if unknown:
        raise ConfigError(f'noma’lum sozlama: {", ".join(unknown)}')
    clean = {}
    for key, value in patch.items():
        if value is None:
            clean[key] = None
        elif key in _RANGES:
            lo, hi, integer = _RANGES[key]
            clean[key] = _number(key, value, lo, hi, integer=integer)
        elif key == 'leaderboard':
            clean[key] = _flag(key, value)
        elif key == 'levels':
            clean[key] = _subset(key, value, LEVELS)
        elif key == 'types':
            clean[key] = _subset(key, value, QTYPES)
    return clean


def current():
    """The config in force right now (defaults + overrides, cached by gamestats)."""
    from gamestats.configs import game_config
    cfg = game_config('word-battle')
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULTS and v is not None})
    return out
