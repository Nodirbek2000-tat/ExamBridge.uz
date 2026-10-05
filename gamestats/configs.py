"""
Per-game tuning the admin can change without a deploy (RUNNER_PLAN §B8.3 "Runner config").

Defaults live in code; Game.config stores only the admin's overrides.

    CONFIG_SCHEMAS[slug](patch) → clean patch        (raises ConfigError: unknown key / bad value)
    CONFIG_DEFAULTS[slug]                             → the defaults
    game_config(slug)                                 → defaults merged with the overrides (cached 5 min)
    merge(current, clean)                             → the new overrides (None removes one = back to default)

PATCH /api/games/stats/admin/games/<slug>/ {config: {...}} validates with the game's schema and
merges into Game.config. A game without a schema has no config (400).

Another game adds its own by shipping `<app>/config.py` with DEFAULTS (dict) and
validate_config(patch) → clean (raising gamestats.configs.ConfigError); it is listed in PLUGINS.
"""
import importlib
import math

from django.core.cache import cache

CACHE_KEY = 'gamestats:config:v1:{}'
CACHE_SECONDS = 5 * 60
LEVELS = ['A1', 'A2', 'B1', 'B2', 'C1']


class ConfigError(ValueError):
    pass


def _number(key, value, lo, hi, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ConfigError(f'{key}: son bo‘lishi kerak')
    if integer and int(value) != value:
        raise ConfigError(f'{key}: butun son bo‘lishi kerak')
    if not lo <= value <= hi:
        raise ConfigError(f'{key}: {lo}–{hi} oralig‘ida bo‘lishi kerak')
    return int(value) if integer else round(float(value), 3)


def _flag(key, value):
    if not isinstance(value, bool):
        raise ConfigError(f'{key}: true yoki false')
    return value


# ── Toby Run ─────────────────────────────────────────────────────────────────

RUNNER_DEFAULTS = {
    'speed_scale': 1.0,
    'window_scale': 1.0,
    'balloon_gap_scale': 1.0,
    'station_every_m': None,          # None = the level's own default
    'server_stt': True,               # False = no Whisper: devices without a recogniser get Listen mode
    'clips_per_run': 30,
    'levels': list(LEVELS),
    'twister': True,
    'revive_voice': True,
    'leaderboard': True,
}

_RUNNER_RANGES = {
    'speed_scale': (0.8, 1.2, False),
    'window_scale': (0.8, 1.5, False),
    'balloon_gap_scale': (0.7, 1.5, False),
    'station_every_m': (400, 1500, True),
    'clips_per_run': (0, 60, True),
}
_RUNNER_FLAGS = ('server_stt', 'twister', 'revive_voice', 'leaderboard')


def validate_runner_config(patch):
    """{key: value} → clean {key: value}. A None value means "back to the default" (kept as None)."""
    if not isinstance(patch, dict):
        raise ConfigError('config obyekt bo‘lishi kerak')
    unknown = sorted(k for k in patch if k not in RUNNER_DEFAULTS)
    if unknown:
        raise ConfigError(f'noma’lum sozlama: {", ".join(map(str, unknown))}')
    clean = {}
    for key, value in patch.items():
        if value is None:
            clean[key] = None
        elif key in _RUNNER_RANGES:
            lo, hi, integer = _RUNNER_RANGES[key]
            clean[key] = _number(key, value, lo, hi, integer=integer)
        elif key in _RUNNER_FLAGS:
            clean[key] = _flag(key, value)
        elif key == 'levels':
            if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
                raise ConfigError('levels: darajalar ro‘yxati, masalan ["A1", "A2"]')
            if any(v not in LEVELS for v in value) or len(set(value)) != len(value):
                raise ConfigError('levels: faqat A1, A2, B1, B2, C1 (takrorsiz)')
            clean[key] = [lv for lv in LEVELS if lv in value]
    return clean


CONFIG_SCHEMAS = {'runner': validate_runner_config}
CONFIG_DEFAULTS = {'runner': RUNNER_DEFAULTS}

# slug → module that may define DEFAULTS + validate_config (loaded when it exists)
PLUGINS = {'word-battle': 'wordbattle.config'}


def _load_plugins():
    for slug, path in PLUGINS.items():
        if slug in CONFIG_SCHEMAS:
            continue
        try:
            mod = importlib.import_module(path)
        except ModuleNotFoundError as e:
            if e.name in (path, path.rsplit('.', 1)[0]):
                continue
            raise
        if hasattr(mod, 'validate_config') and isinstance(getattr(mod, 'DEFAULTS', None), dict):
            CONFIG_SCHEMAS[slug] = mod.validate_config
            CONFIG_DEFAULTS[slug] = mod.DEFAULTS


def schema_for(slug):
    _load_plugins()
    return CONFIG_SCHEMAS.get(slug)


def defaults_for(slug):
    _load_plugins()
    return dict(CONFIG_DEFAULTS.get(slug, {}))


def merge(current, clean):
    """Overrides after a validated patch (None drops the override: back to the default)."""
    out = dict(current or {})
    for key, value in clean.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


def effective(slug, overrides):
    eff = defaults_for(slug)
    for key, value in (overrides or {}).items():
        if key in eff:                       # a key a newer schema dropped is ignored, never served
            eff[key] = value
    return eff


def game_config(slug):
    """The config a game should use right now (defaults + overrides). Cached; dropped on every PATCH."""
    key = CACHE_KEY.format(slug)
    try:
        hit = cache.get(key)
    except Exception:
        hit = None
    if hit is not None:
        return hit
    from .models import Game
    overrides = Game.objects.filter(slug=slug).values_list('config', flat=True).first() or {}
    eff = effective(slug, overrides)
    try:
        cache.set(key, eff, CACHE_SECONDS)
    except Exception:
        pass
    return eff


def bust_config_cache(slug):
    try:
        cache.delete(CACHE_KEY.format(slug))
    except Exception:
        pass
