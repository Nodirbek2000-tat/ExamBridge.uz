"""
The rules a lesson question must meet, for admins and for the AI alike.

A question sits after a step of the lesson (`step`) or in the homework
(`is_homework`), never both. Multiple choice is marked here at once; a written
answer is only saved until something can mark it.
"""
from .models import LessonQuestion

MAX_PROMPT = 1000
MAX_OPTION = 300
MIN_OPTIONS = 2
MAX_OPTIONS = 6
MAX_NOTE = 2000
MAX_ANSWER_TEXT = 2000


class QuestionError(ValueError):
    """The question breaks the rules above."""


def _optional_text(data, key):
    value = str(data.get(key) or '').strip()
    if len(value) > MAX_NOTE:
        raise QuestionError(f'{key.capitalize()} can be at most {MAX_NOTE} characters.')
    return value


def _clean_choices(data):
    """(options, index of the correct one)."""
    raw = data.get('options')
    if not isinstance(raw, list) or not MIN_OPTIONS <= len(raw) <= MAX_OPTIONS:
        raise QuestionError(f'Give {MIN_OPTIONS} to {MAX_OPTIONS} options.')
    options = [str(o).strip() for o in raw]
    if not all(options):
        raise QuestionError('Options cannot be empty.')
    if any(len(o) > MAX_OPTION for o in options):
        raise QuestionError(f'An option can be at most {MAX_OPTION} characters.')
    if len(set(options)) != len(options):
        raise QuestionError('Options must be different.')

    correct = data.get('correct')
    if isinstance(correct, bool) or not isinstance(correct, int) or not 0 <= correct < len(options):
        raise QuestionError('Choose the correct option.')
    return options, correct


def clean_content(data):
    """The question itself, wherever it ends up. Raises QuestionError."""
    if not isinstance(data, dict):
        raise QuestionError('A question must be an object.')
    kind = data.get('kind', LessonQuestion.Kind.CHOICE)
    if kind not in LessonQuestion.Kind.values:
        raise QuestionError('Unknown question type.')

    prompt = str(data.get('prompt') or '').strip()
    if not prompt:
        raise QuestionError('Question text is required.')
    if len(prompt) > MAX_PROMPT:
        raise QuestionError(f'Question text can be at most {MAX_PROMPT} characters.')

    options, correct = [], None
    if kind == LessonQuestion.Kind.CHOICE:
        options, correct = _clean_choices(data)

    return {
        'kind': kind,
        'prompt': prompt,
        'options': options,
        'correct': correct,
        'rubric': _optional_text(data, 'rubric'),
        'explanation': _optional_text(data, 'explanation'),
    }


def clean_question(data, lesson):
    """Fields for a LessonQuestion of this lesson. Raises QuestionError."""
    fields = clean_content(data)
    is_homework = bool(data.get('is_homework'))
    step = None
    if not is_homework:
        step = lesson.steps.filter(pk=data.get('step')).first() if data.get('step') else None
        if step is None:
            raise QuestionError('Choose the step this question follows.')
    return {**fields, 'is_homework': is_homework, 'step': step}


def mark(question, choice, text):
    """(choice, text, is_correct) of a valid answer. Raises QuestionError."""
    if question.kind == LessonQuestion.Kind.CHOICE:
        valid = isinstance(choice, int) and not isinstance(choice, bool) and 0 <= choice < len(question.options)
        if not valid:
            raise QuestionError('Choose one of the options.')
        return choice, '', choice == question.correct

    text = str(text or '').strip()
    if not text:
        raise QuestionError('Write your answer.')
    if len(text) > MAX_ANSWER_TEXT:
        raise QuestionError(f'An answer can be at most {MAX_ANSWER_TEXT} characters.')
    return None, text, None
