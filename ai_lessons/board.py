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
    {"type": "icon", "name": "scale", "label": "Balance", "color": "green"}
    {"type": "secret", "text": "..."}                 an exam secret: a trap or a shortcut
    {"type": "fix", "wrong": "He don't", "right": "He doesn't"}   a mistake crossed out, then fixed

A step may also have `think`: seconds of quiet for the student after a step that
ends with a question to them (0 = none, at most MAX_THINK).

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

# Pictures the board can draw (the player has a drawing for each name).
ICONS = (
    'scale', 'calculator', 'lightbulb', 'target', 'clock', 'check', 'cross', 'warning', 'pencil',
    'ruler', 'book', 'brain', 'puzzle', 'key', 'trophy', 'flag', 'users', 'globe', 'compass',
    'chart-line', 'chart-bar', 'chart-pie', 'sigma', 'percent', 'divide', 'equal', 'arrow-right',
    'repeat', 'search', 'message', 'mic', 'headphones', 'pen', 'layers', 'timer', 'star', 'zap',
    'coins', 'cart', 'car', 'house', 'tree', 'sun', 'droplet', 'graduation', 'question',
)

MAX_ELEMENTS = 12
MAX_TEXT = 400
MAX_ITEMS = 8
MAX_TEX = 300
MAX_SHAPES = 40
MAX_PATH = 2000
MAX_CANVAS = 2000
MAX_SAY = 4000
MAX_FIX = 200
MAX_THINK = 8

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
    if kind in ('note', 'secret'):
        return {'type': kind, 'text': _text(raw.get('text'), kind.capitalize())}
    if kind == 'fix':
        return {
            'type': kind,
            'wrong': _text(raw.get('wrong'), 'The wrong version', MAX_FIX),
            'right': _text(raw.get('right'), 'The right version', MAX_FIX),
        }
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
    if kind == 'icon':
        name = raw.get('name')
        if name not in ICONS:
            raise BoardError(f'Unknown icon: {name}. Use one of: {", ".join(ICONS)}.')
        element = {'type': kind, 'name': name, 'color': _color(raw)}
        if raw.get('label'):
            element['label'] = _text(raw.get('label'), 'Icon label', 40)
        return element
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


def _short(value, limit):
    if isinstance(value, str) and len(value.strip()) > limit:
        return value.strip()[:limit - 1].rstrip() + '…'
    return value


def _mend(item):
    """An element with the small slips the AI makes fixed: long text, unknown colour, long lists."""
    item = dict(item)
    if 'color' in item and item['color'] not in COLORS:
        item.pop('color')
    for key, limit in (('text', MAX_TEXT), ('label', 40), ('wrong', MAX_FIX), ('right', MAX_FIX)):
        if key in item:
            item[key] = _short(item[key], limit)
    if isinstance(item.get('items'), list):
        item['items'] = [_short(i, MAX_TEXT) for i in item['items'][:MAX_ITEMS]]
    if item.get('type') == 'diagram' and isinstance(item.get('shapes'), list):
        item['shapes'] = [
            {k: v for k, v in s.items() if k != 'color' or v in COLORS} if isinstance(s, dict) else s
            for s in item['shapes'][:MAX_SHAPES]
        ]
    return item


def repair_step(raw):
    """
    What the AI wrote for a step, mended where that is safe, so one stray element does
    not throw away a whole section: fixable slips are fixed, elements that still break
    the rules are left out, and times are put in order. The spoken text is never changed.
    """
    if not isinstance(raw, dict):
        return raw
    think = raw.get('think')
    if isinstance(think, bool) or not isinstance(think, (int, float)) or not math.isfinite(think):
        think = 0
    raw = {**raw, 'think': min(MAX_THINK, max(0, round(think)))}
    board = raw.get('board')
    if not isinstance(board, list):
        return {**raw, 'board': []}

    kept, previous = [], 0
    for item in board:
        if not isinstance(item, dict):
            continue
        item = _mend(item)
        try:
            _clean_element(item)
        except BoardError:
            continue
        at = item.get('at')
        if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at):
            at = previous
        at = min(1, max(previous, at))
        kept.append({**item, 'at': at})
        previous = at
        if len(kept) == MAX_ELEMENTS:
            break
    return {**raw, 'board': kept}


def clean_step(step):
    """A safe {say, board, think} copy of one step. Raises BoardError when it breaks the rules."""
    if not isinstance(step, dict):
        raise BoardError('A step must be an object.')
    think = step.get('think') or 0
    return {
        'say': _text(step.get('say'), 'Step text', MAX_SAY),
        'board': clean_board(step.get('board', [])),
        'think': round(_number(think, 'Think time', 0, MAX_THINK)),
    }


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
