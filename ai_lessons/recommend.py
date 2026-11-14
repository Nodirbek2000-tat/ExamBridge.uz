"""
Lessons for a student's weak spots.

The weak spots come from the same numbers as the analytics pages, plus the
mistakes the AI marked in the student's writing and speaking:

    SAT         the topics answered worst
    IELTS/CEFR  the question types answered worst, and writing / speaking
                criteria below target
    everywhere  grammar themes the AI keeps finding in their essays and answers

A published lesson matches a spot when its topics (chosen by the AI that wrote
it, or by an admin) or its title share the spot's key words.
"""
import re
from collections import Counter
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from .models import AILesson, LessonProgress

RANGE = '90d'
WEAK_ACCURACY = 70            # below this a topic or question type is weak
MIN_SAT_ATTEMPTS = 3
IELTS_TARGET_BAND = 6.5
CEFR_TARGET = 3.5             # criteria are out of 5
MIN_THEME_MISTAKES = 2
RECENT_RESPONSES = 30
MAX_SPOTS = 6
MAX_LESSONS = 3
CACHE_SECONDS = 600

EXAMS = ('sat', 'ielts', 'cefr')

# What the AI calls an error in writing and speaking → the grammar theme a lesson teaches.
GRAMMAR_THEMES = {
    'Present simple': ('present simple', 'third person', 'third-person', 'subject-verb', 'subject verb', 'verb agreement'),
    'Past tenses': ('past simple', 'past tense', 'irregular verb', 'past continuous', 'past perfect'),
    'Present perfect': ('present perfect',),
    'Verb tenses': ('tense',),
    'Articles': ('article',),
    'Prepositions': ('preposition',),
    'Plurals and countable nouns': ('plural', 'countable', 'uncountable', 'singular'),
    'Word order': ('word order',),
    'Conditionals': ('conditional',),
    'Passive voice': ('passive',),
    'Relative clauses': ('relative clause', 'relative pronoun'),
    'Modal verbs': ('modal',),
    'Word choice and collocations': ('collocation', 'word choice', 'wrong word', 'word form'),
    'Spelling': ('spelling', 'misspel'),
    'Punctuation': ('punctuation', 'comma', 'run-on', 'comma splice'),
    'Linking words': ('linking', 'connector', 'cohesive device', 'transition'),
}

# Which lesson sections a spot's area belongs to.
AREA_SECTIONS = {
    'Math': {'math'}, 'Reading & Writing': {'reading_writing', 'grammar'},
    'Reading': {'reading'}, 'Listening': {'listening'}, 'Writing': {'writing', 'grammar'},
    'Speaking': {'speaking', 'grammar'}, 'Grammar': {'grammar', 'writing', 'speaking'},
}
STOP_WORDS = {
    'the', 'and', 'for', 'with', 'from', 'your', 'you', 'are', 'how', 'what', 'into', 'one', 'two', 'its',
    'sat', 'ielts', 'cefr', 'exam', 'test', 'lesson', 'part', 'task', 'question', 'questions', 'answer',
    'skill', 'skills', 'level', 'band', 'strategy', 'strategies', 'tips',
}
WORD = re.compile(r'[a-z]+')


def _stem(word):
    return word[:-1] if len(word) > 4 and word.endswith('s') else word


def key_words(text):
    return {_stem(w) for w in WORD.findall((text or '').lower()) if len(w) > 2 and w not in STOP_WORDS}


def _spot(name, area, result, subjects, extra_words=()):
    return {'name': name, 'area': area, 'result': result, 'subjects': subjects,
            'words': sorted(key_words(name) | {_stem(w) for words in extra_words for w in key_words(words)})}


# ── Weak spots ────────────────────────────────────────────────────────────────

def _sat_spots(user):
    from api.sat_analytics import build_analytics
    data = build_analytics(user, RANGE)
    return [
        _spot(s['topic'], s['section_label'], f"{s['correct']}/{s['attempts']} ({s['accuracy']}%)", ('SAT',))
        for s in data['lowest_skills']
        if s['topic'] and s['attempts'] >= MIN_SAT_ATTEMPTS and s['accuracy'] < WEAK_ACCURACY
    ]


def _skill_spots(user, exam):
    from api.exam_analytics import build_exam_analytics
    data = build_exam_analytics(user, exam.upper(), RANGE)
    subjects = (exam.upper(),)
    spots = [
        _spot(t['label'], t['skill'].title(), f"{t['correct']}/{t['attempts']} ({t['accuracy']}%)", subjects)
        for t in data['weakest_types'] if t['accuracy'] < WEAK_ACCURACY
    ]
    for skill in ('writing', 'speaking'):
        part = data.get(skill)
        if not part or not part.get('count'):
            continue
        cefr = part.get('scale') == 'cefr'
        target = CEFR_TARGET if cefr else IELTS_TARGET_BAND
        low = sorted((c for c in part['criteria'] if c['avg'] is not None and c['avg'] < target), key=lambda c: c['avg'])
        for c in low[:2]:
            result = f"{c['avg']}/5" if cefr else f"band {c['avg']}"
            spots.append(_spot(c['label'], skill.title(), f'{skill.title()}: {result}', subjects))
    return spots


def _issues(value):
    """Every 'issue' the AI wrote under 'errors', wherever it sits in a stored result."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'errors' and isinstance(item, list):
                for error in item:
                    if isinstance(error, dict) and error.get('issue'):
                        yield str(error['issue'])
            else:
                yield from _issues(item)
    elif isinstance(value, list):
        for item in value:
            yield from _issues(item)


def grammar_themes(stored_results):
    """How often each grammar theme comes up in the AI's error notes."""
    themes = Counter()
    for issue in (i.lower() for result in stored_results for i in _issues(result)):
        theme = next((name for name, marks in GRAMMAR_THEMES.items() if any(m in issue for m in marks)), None)
        if theme:
            themes[theme] += 1
    return themes


def _mistake_spots(user):
    """Grammar themes the AI keeps marking in this student's writing and speaking."""
    from cefr.models import CEFRSpeakingResponse, CEFRWritingResponse
    from ielts.models import SpeakingResponse, WritingResponse

    since = timezone.now() - timedelta(days=90)
    stored = [
        *WritingResponse.objects.filter(attempt__user=user, created_at__gte=since)
        .order_by('-created_at').values_list('ai_criteria', flat=True)[:RECENT_RESPONSES],
        *SpeakingResponse.objects.filter(attempt__user=user, created_at__gte=since)
        .order_by('-created_at').values_list('ai_criteria', flat=True)[:RECENT_RESPONSES],
        *CEFRWritingResponse.objects.filter(user=user, submitted_at__gte=since)
        .order_by('-submitted_at').values_list('result', flat=True)[:RECENT_RESPONSES],
        *CEFRSpeakingResponse.objects.filter(user=user, submitted_at__gte=since)
        .order_by('-submitted_at').values_list('result', flat=True)[:RECENT_RESPONSES],
    ]
    themes = grammar_themes(stored)
    return [
        _spot(theme, 'Grammar', f'{count} mistakes in your writing and speaking', ('IELTS', 'CEFR'), GRAMMAR_THEMES[theme])
        for theme, count in themes.most_common() if count >= MIN_THEME_MISTAKES
    ]


def weak_spots(user, exams=EXAMS):
    spots = []
    for exam in exams:
        spots += _sat_spots(user) if exam == 'sat' else _skill_spots(user, exam)
    if any(exam != 'sat' for exam in exams):
        spots += _mistake_spots(user)
    return spots[:MAX_SPOTS]


# ── Matching lessons ──────────────────────────────────────────────────────────

def _score(lesson, spot):
    if lesson.subject not in (*spot['subjects'], AILesson.Subject.GENERAL):
        # grammar is grammar: an IELTS student's tense mistakes are taught in a CEFR lesson too
        if not (spot['area'] == 'Grammar' and lesson.subject in ('IELTS', 'CEFR')):
            return 0
    topics = [t.lower() for t in lesson.topics or []]
    if spot['name'].lower() in topics:
        return 10
    shared = set(spot['words']) & key_words(' '.join([lesson.title, *topics]))
    if not shared:
        return 0
    return len(shared) + (1 if lesson.section in AREA_SECTIONS.get(spot['area'], ()) else 0)


def matching_lessons(user, spots):
    """Up to MAX_LESSONS published lessons for these spots, each with the spots it covers."""
    if not spots:
        return []
    subjects = {s for spot in spots for s in spot['subjects']} | {AILesson.Subject.GENERAL, 'CEFR', 'IELTS'}
    lessons = list(AILesson.objects.filter(status=AILesson.Status.PUBLISHED, subject__in=subjects))
    done = set(LessonProgress.objects.filter(user=user, completed_at__isnull=False).values_list('lesson_id', flat=True))
    found = []
    for lesson in lessons:
        scored = sorted(((_score(lesson, spot), spot) for spot in spots), key=lambda x: -x[0])
        covers = [spot for score, spot in scored if score > 0]
        if covers:
            found.append((lesson.id in done, -scored[0][0], -len(covers), lesson, covers))
    found.sort(key=lambda row: row[:3])
    return [(lesson, covers) for *_, lesson, covers in found[:MAX_LESSONS]]


def recommendations(user, exam=None):
    """{'spots': [...], 'lessons': [(lesson, covers)]} — remembered for a few minutes per student."""
    exams = (exam,) if exam in EXAMS else EXAMS
    key = f'ai-lessons-recommend:{user.id}:{exam or "all"}'
    cached = cache.get(key)
    if cached is not None:
        spots, ids = cached
    else:
        spots = weak_spots(user, exams)
        ids = [(lesson.id, [c['name'] for c in covers]) for lesson, covers in matching_lessons(user, spots)]
        cache.set(key, (spots, ids), CACHE_SECONDS)
    lessons = AILesson.objects.in_bulk([i for i, _ in ids])
    by_name = {s['name']: s for s in spots}
    return {
        'spots': spots,
        'lessons': [
            (lessons[i], [by_name[n] for n in names if n in by_name])
            for i, names in ids if i in lessons and lessons[i].status == AILesson.Status.PUBLISHED
        ],
    }


def known_topics(lesson):
    """Skill names the analytics use, so the AI that writes a lesson can tag it with them."""
    from api.exam_analytics import QTYPE_LABEL, SPEAKING_CRITERIA, WRITING_CRITERIA

    if lesson.subject == AILesson.Subject.SAT:
        names = cache.get('ai-lessons-sat-topics')
        if names is None:
            from tests_app.models import BankQuestion, Question
            names = sorted({
                t.strip() for t in [*Question.objects.exclude(topic='').values_list('topic', flat=True).distinct(),
                                    *BankQuestion.objects.exclude(topic='').values_list('topic', flat=True).distinct()]
                if t and t.strip()
            })[:120]
            cache.set('ai-lessons-sat-topics', names, 3600)
        return names
    names = [*QTYPE_LABEL.values(), *(label for _, label, _ in WRITING_CRITERIA), *(label for _, label, _ in SPEAKING_CRITERIA)]
    if lesson.subject == AILesson.Subject.CEFR:
        from api.cefr_speaking import CRITERIA as CEFR_SPEAKING
        from api.cefr_writing import CRITERIA as CEFR_WRITING
        names += [label for _, label in (*CEFR_WRITING, *CEFR_SPEAKING)]
    return sorted(set(names) | set(GRAMMAR_THEMES))


def clean_topics(raw):
    if not isinstance(raw, list):
        return []
    topics = []
    for item in raw[:6]:
        item = str(item or '').strip()[:60]
        if item and item.lower() not in {t.lower() for t in topics}:
            topics.append(item)
    return topics
