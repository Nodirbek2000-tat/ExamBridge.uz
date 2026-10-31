"""
What may appear on the teaching board, and the check every lesson step passes.

LessonStep.board is a list of elements written top to bottom. Each one has `at`:
when it appears, as a share of the step (0–1), so the timing still holds whatever
length the recorded voice turns out to be.

    {"type": "heading", "text": "...", "at": 0}
    {"type": "text", "text": "...", "color": "yellow"}
    {"type": "list", "items": ["...", "..."]}
    {"type": "math", "tex": "y = mx + b"}
    {"type": "note", "text": "..."}
    {"type": "diagram", "width": 400, "height": 300, "shapes": [...]}

Text may use **bold** and inline math \\( x^2 \\). Diagram shapes are drawn in order:

    {"kind": "line", "from": [0, 0], "to": [10, 10], "arrow": true, "dashed": true}
    {"kind": "circle", "center": [5, 5], "r": 3, "filled": true}
    {"kind": "path", "d": "M0 0 L10 10"}
    {"kind": "label", "pos": [5, 5], "text": "A"}

The AI writes this, so it is checked rather than trusted: only known fields are
kept, and the player can render whatever passes.
"""
import math
import re

from django.db import transaction

from .models import LessonStep

COLORS = ('chalk', 'yellow', 'sky', 'pink', 'green')

MAX_ELEMENTS = 12
MAX_TEXT = 400
MAX_ITEMS = 8
MAX_TEX = 300
MAX_SHAPES = 40
MAX_PATH = 2000
MAX_CANVAS = 2000
MAX_SAY = 4000

PATH_DATA = re.compile(r'^[MLHVCSQTAZmlhvcsqtaz0-9\s,.\-]+$')


class BoardError(ValueError):
    """The board or step breaks the rules above."""


def _text(value, label, limit=MAX_TEXT):
    if not isinstance(value, str) or not value.strip():
        raise BoardError(f'{label} must be non-empty text.')
    value = value.strip()
    if len(value) > limit:
        raise BoardError(f'{label} can be at most {limit} characters.')
    return value


def _number(value, label, low=-MAX_CANVAS, high=MAX_CANVAS):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BoardError(f'{label} must be a number.')
    if not low <= value <= high:
        raise BoardError(f'{label} must be between {low} and {high}.')
    return value


def _point(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise BoardError(f'{label} must be [x, y].')
    return [_number(value[0], label), _number(value[1], label)]


def _color(raw):
    color = raw.get('color', COLORS[0])
    if color not in COLORS:
        raise BoardError(f'Unknown color: {color}.')
    return color


def _clean_shape(raw):
    if not isinstance(raw, dict):
        raise BoardError('A shape must be an object.')
    kind = raw.get('kind')
    shape = {'kind': kind, 'color': _color(raw)}
    if kind == 'line':
        shape['from'] = _point(raw.get('from'), 'Line start')
        shape['to'] = _point(raw.get('to'), 'Line end')
        shape['arrow'] = bool(raw.get('arrow'))
        shape['dashed'] = bool(raw.get('dashed'))
    elif kind == 'circle':
        shape['center'] = _point(raw.get('center'), 'Circle center')
        shape['r'] = _number(raw.get('r'), 'Circle radius', 0.1, MAX_CANVAS)
        shape['filled'] = bool(raw.get('filled'))
    elif kind == 'path':
        d = _text(raw.get('d'), 'Path', MAX_PATH)
        if not PATH_DATA.match(d):
            raise BoardError('Path has characters that are not path commands.')
        shape['d'] = d
    elif kind == 'label':
        shape['pos'] = _point(raw.get('pos'), 'Label position')
        shape['text'] = _text(raw.get('text'), 'Label', 60)
    else:
        raise BoardError(f'Unknown shape: {kind}.')
    return shape


def _clean_diagram(raw):
    shapes = raw.get('shapes')
    if not isinstance(shapes, list) or not shapes:
        raise BoardError('A diagram needs shapes.')
    if len(shapes) > MAX_SHAPES:
        raise BoardError(f'A diagram can have at most {MAX_SHAPES} shapes.')
    return {
        'width': _number(raw.get('width'), 'Diagram width', 1, MAX_CANVAS),
        'height': _number(raw.get('height'), 'Diagram height', 1, MAX_CANVAS),
        'shapes': [_clean_shape(s) for s in shapes],
    }


def _clean_element(raw):
    if not isinstance(raw, dict):
        raise BoardError('A board element must be an object.')
    kind = raw.get('type')
    if kind == 'heading':
        return {'type': kind, 'text': _text(raw.get('text'), 'Heading')}
    if kind == 'text':
        return {'type': kind, 'text': _text(raw.get('text'), 'Text'), 'color': _color(raw)}
    if kind == 'note':
        return {'type': kind, 'text': _text(raw.get('text'), 'Note')}
    if kind == 'math':
        return {'type': kind, 'tex': _text(raw.get('tex'), 'Formula', MAX_TEX)}
    if kind == 'list':
        items = raw.get('items')
        if not isinstance(items, list) or not items:
            raise BoardError('A list needs items.')
        if len(items) > MAX_ITEMS:
            raise BoardError(f'A list can have at most {MAX_ITEMS} items.')
        return {'type': kind, 'items': [_text(i, 'List item') for i in items], 'color': _color(raw)}
    if kind == 'diagram':
        return {'type': kind, **_clean_diagram(raw)}
    raise BoardError(f'Unknown board element: {kind}.')


def clean_board(raw):
    """A safe copy of a step's board. Raises BoardError when it breaks the rules."""
    if not isinstance(raw, list):
        raise BoardError('The board must be a list.')
    if len(raw) > MAX_ELEMENTS:
        raise BoardError(f'A step can have at most {MAX_ELEMENTS} board elements.')

    board, previous = [], 0
    for i, item in enumerate(raw):
        element = _clean_element(item)
        at = item.get('at')
        if at is None:
            at = max(previous, i / len(raw))
        at = _number(at, 'Board time', 0, 1)
        if at < previous:
            raise BoardError('Board elements must appear in order.')
        element['at'] = at
        previous = at
        board.append(element)
    return board


def clean_step(step):
    """A safe {say, board} copy of one step. Raises BoardError when it breaks the rules."""
    if not isinstance(step, dict):
        raise BoardError('A step must be an object.')
    return {'say': _text(step.get('say'), 'Step text', MAX_SAY), 'board': clean_board(step.get('board', []))}


def replace_steps(lesson, steps):
    """
    Swap a lesson's steps for new ones, all or nothing. Every step is checked before
    anything is touched, so a bad step leaves the lesson as it was.
    """
    if not isinstance(steps, list) or not steps:
        raise BoardError('A lesson needs at least one step.')
    cleaned = [LessonStep(lesson=lesson, order=i, **clean_step(step)) for i, step in enumerate(steps)]
    old_audio = [s.audio for s in lesson.steps.exclude(audio='')]

    def remove_old_audio():
        for audio in old_audio:
            audio.delete(save=False)

    with transaction.atomic():
        lesson.steps.all().delete()
        created = LessonStep.objects.bulk_create(cleaned)
        # After the commit: a rollback further up must not leave steps without their sound.
        transaction.on_commit(remove_old_audio)
    return created
