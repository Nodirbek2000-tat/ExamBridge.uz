"""
The rules a lesson question must meet, for admins and for the AI alike.

A question sits in exactly one place: after a step of the lesson (`step`), in the
final test at the end of the lesson (`is_quiz`), or in the homework (`is_homework`).

    CHOICE      options, correct = index of the right one
    TRUE_FALSE  a statement; options are True, False and correct is 0 (true) or 1 (false)
    GAP         the prompt has one gap written as ___; accepted = every answer that counts
    MISTAKE     options are the parts of a sentence or of a solution; correct = the wrong part
    WRITTEN     a free answer, checked by the AI teacher against the rubric

Everything but WRITTEN is marked here at once.

Any question may also have a `hint` (a nudge, never the answer) and `wrong_say`:
what the teacher says aloud to a student who got it wrong (the AI also sends it
as `say_if_wrong`). The voice job records it.
"""
import re
import unicodedata
from fractions import Fraction

from .models import LessonQuestion

Kind = LessonQuestion.Kind

MAX_PROMPT = 1000
MAX_OPTION = 300
MIN_OPTIONS = 2
MAX_OPTIONS = 6
MIN_PARTS = 3
MAX_PARTS = 8
MAX_ACCEPTED = 8
MAX_GAP_ANSWER = 100
MAX_NOTE = 2000
MAX_ANSWER_TEXT = 2000
MAX_GAP_TEXT = 200
MAX_HINT = 300
MAX_WRONG_SAY = 700

TRUE_FALSE = ['True', 'False']
GAP = re.compile(r'_{3,}')
NUMBER = re.compile(r'^-?\d+(\.\d+)?$|^-?\d+/\d+$')
QUOTES = str.maketrans({'’': "'", '‘': "'", '`': "'", '´': "'", '“': '"', '”': '"'})


class QuestionError(ValueError):
    """The question breaks the rules above."""


def _optional_text(data, key):
    value = str(data.get(key) or '').strip()
    if len(value) > MAX_NOTE:
        raise QuestionError(f'{key.capitalize()} can be at most {MAX_NOTE} characters.')
    return value


def _index(value, size, message):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < size:
        raise QuestionError(message)
    return value


def _clean_list(raw, low, high, what, unique):
    if not isinstance(raw, list) or not low <= len(raw) <= high:
        raise QuestionError(f'Give {low} to {high} {what}.')
    items = [str(o).strip() for o in raw]
    if not all(items):
        raise QuestionError(f'{what.capitalize()} cannot be empty.')
    if any(len(o) > MAX_OPTION for o in items):
        raise QuestionError(f'One of the {what} is longer than {MAX_OPTION} characters.')
    if unique and len(set(items)) != len(items):
        raise QuestionError(f'The {what} must be different.')
    return items


def _true_false(data):
    correct = data.get('correct')
    if isinstance(correct, bool):              # the AI may say true / false outright
        return 0 if correct else 1
    return _index(correct, 2, 'Say whether the statement is true or false.')


def _accepted(data, prompt):
    if len(GAP.findall(prompt)) != 1:
        raise QuestionError('Write exactly one gap as ___ in the question.')
    raw = data.get('accepted')
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ACCEPTED:
        raise QuestionError(f'Give 1 to {MAX_ACCEPTED} accepted answers for the gap.')
    accepted = []
    for item in raw:
        item = str(item).strip()
        if not item or len(item) > MAX_GAP_ANSWER:
            raise QuestionError(f'An accepted answer must be 1 to {MAX_GAP_ANSWER} characters.')
        if item not in accepted:
            accepted.append(item)
    return accepted


def clean_content(data):
    """The question itself, wherever it ends up. Raises QuestionError."""
    if not isinstance(data, dict):
        raise QuestionError('A question must be an object.')
    kind = data.get('kind', Kind.CHOICE)
    if kind not in Kind.values:
        raise QuestionError('Unknown question type.')

    prompt = str(data.get('prompt') or '').strip()
    if not prompt:
        raise QuestionError('Question text is required.')
    if len(prompt) > MAX_PROMPT:
        raise QuestionError(f'Question text can be at most {MAX_PROMPT} characters.')

    options, correct, accepted = [], None, []
    if kind == Kind.CHOICE:
        options = _clean_list(data.get('options'), MIN_OPTIONS, MAX_OPTIONS, 'options', unique=True)
        correct = _index(data.get('correct'), len(options), 'Choose the correct option.')
    elif kind == Kind.TRUE_FALSE:
        options, correct = list(TRUE_FALSE), _true_false(data)
    elif kind == Kind.MISTAKE:
        # the same word may well appear twice in a sentence
        options = _clean_list(data.get('options'), MIN_PARTS, MAX_PARTS, 'parts', unique=False)
        correct = _index(data.get('correct'), len(options), 'Mark the part with the mistake.')
    elif kind == Kind.GAP:
        accepted = _accepted(data, prompt)

    hint = str(data.get('hint') or '').strip()
    if len(hint) > MAX_HINT:
        raise QuestionError(f'A hint can be at most {MAX_HINT} characters.')
    wrong_say = str(data.get('wrong_say') or data.get('say_if_wrong') or '').strip()
    if len(wrong_say) > MAX_WRONG_SAY:
        raise QuestionError(f'What the teacher says can be at most {MAX_WRONG_SAY} characters.')

    return {
        'kind': kind,
        'prompt': prompt,
        'options': options,
        'correct': correct,
        'accepted': accepted,
        'rubric': _optional_text(data, 'rubric'),
        'explanation': _optional_text(data, 'explanation'),
        'hint': hint,
        'wrong_say': wrong_say,
    }


def clean_question(data, lesson):
    """Fields for a LessonQuestion of this lesson. Raises QuestionError."""
    fields = clean_content(data)
    is_homework = bool(data.get('is_homework'))
    is_quiz = bool(data.get('is_quiz')) and not is_homework
    step = None
    if not is_homework and not is_quiz:
        step = lesson.steps.filter(pk=data.get('step')).first() if data.get('step') else None
        if step is None:
            raise QuestionError('Choose the step this question follows.')
    return {**fields, 'is_homework': is_homework, 'is_quiz': is_quiz, 'step': step}


# ── Marking ───────────────────────────────────────────────────────────────────

def _plain(text):
    """An answer as compared: same letters, quotes and spaces, no closing full stop."""
    text = unicodedata.normalize('NFKC', str(text)).translate(QUOTES).casefold()
    return re.sub(r'\s+', ' ', text).strip(' .!?,;:"')


def _number(text):
    text = text.replace(' ', '')
    if not NUMBER.match(text):
        return None
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None


def gap_matches(answer, accepted):
    """Whether a gap answer is one of the accepted ones: 0.5 = 1/2, She's = she's."""
    given = _plain(answer)
    number = _number(given)
    for option in accepted:
        option = _plain(option)
        if given == option:
            return True
        if number is not None and number == _number(option):
            return True
    return False


def mark(question, choice, text):
    """(choice, text, is_correct) of a valid answer. Raises QuestionError."""
    if question.kind in (Kind.CHOICE, Kind.TRUE_FALSE, Kind.MISTAKE):
        valid = isinstance(choice, int) and not isinstance(choice, bool) and 0 <= choice < len(question.options)
        if not valid:
            raise QuestionError('Choose one of the options.')
        return choice, '', choice == question.correct

    text = str(text or '').strip()
    if not text:
        raise QuestionError('Write your answer.')
    if question.kind == Kind.GAP:
        if len(text) > MAX_GAP_TEXT:
            raise QuestionError(f'An answer can be at most {MAX_GAP_TEXT} characters.')
        return None, text, gap_matches(text, question.accepted)
    if len(text) > MAX_ANSWER_TEXT:
        raise QuestionError(f'An answer can be at most {MAX_ANSWER_TEXT} characters.')
    return None, text, None
