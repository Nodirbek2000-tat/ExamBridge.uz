"""
SAT analytics — every number on the Analytics page comes from here.

Two sources are merged into one list of "events" (one answered question each):
  • test answers     (tests_app.Answer)            — full tests and single modules, with time spent
  • question bank    (tests_app.SavedBankQuestion) — practice, no timing

The AI analysis (api/analytics_ai.py) is given the SAME aggregated numbers
via _ai_facts and is told to use only them, so its text cannot disagree with the charts.
"""
from collections import defaultdict
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from tests_app.models import Answer, SavedBankQuestion, SavedQuestion

RANGES = {'7d': 7, '30d': 30, '90d': 90, 'all': None}
MAX_Q_SECONDS = 600          # an idle tab must not count as 40 minutes on one question
SESSION_GAP = 30 * 60        # a pause longer than this starts a new sitting
MIN_SKILL_ATTEMPTS = 3       # fewer tries than this is noise, not a weak skill
HEATMAP_DAYS = 153           # ~5 months
AI_RELIABLE_ATTEMPTS = 5     # below this an accuracy is an anecdote — the model is told so
STRONG_ACCURACY = 70         # a 'strongest skill' must actually be good

# Domain keys differ between test questions and bank questions — one label for both
DOMAINS = {
    'ENGLISH': [
        ('Information and Ideas', {'information_and_ideas', 'info_ideas', 'information_ideas'}),
        ('Craft and Structure', {'craft_and_structure', 'craft_structure'}),
        ('Expression of Ideas', {'expression_of_ideas', 'expression_ideas'}),
        ('Standard English Conventions', {'standard_english', 'standard_english_conventions'}),
    ],
    'MATH': [
        ('Algebra', {'algebra'}),
        ('Advanced Math', {'advanced_math'}),
        ('Problem-Solving and Data Analysis', {'problem_data', 'problem_solving', 'problem_solving_and_data_analysis', 'problem_solving_data_analysis'}),
        ('Geometry and Trigonometry', {'geometry', 'geometry_trigonometry', 'geometry_and_trigonometry'}),
    ],
}
_DOMAIN_LOOKUP = {sec: {k: label for label, keys in rows for k in keys} for sec, rows in DOMAINS.items()}
SECTION_LABEL = {'ENGLISH': 'Reading & Writing', 'MATH': 'Math'}
ACTIVITY_LABEL = {'test': 'Full-length tests', 'module': 'Module practice', 'bank': 'Question bank'}
DIFFICULTIES = ['EASY', 'MEDIUM', 'HARD']


def _domain(section, raw):
    key = str(raw or '').strip().lower().replace('-', '_').replace(' ', '_').replace('&', 'and')
    return _DOMAIN_LOOKUP.get(section, {}).get(key)


def _pct(correct, total):
    return round(correct * 100 / total) if total else None


def _collect_events(user):
    """Every question the user answered, oldest first."""
    events = []

    answers = (
        Answer.objects.filter(attempt__user=user, is_skipped=False)
        .filter(Q(selected_choice__isnull=False) | ~Q(text_answer=''))
        .values('is_correct', 'time_spent', 'answered_at', 'attempt__is_individual',
                'question__difficulty', 'question__category', 'question__topic',
                'question__module__section__section_type')
    )
    for a in answers:
        section = a['question__module__section__section_type'] or 'ENGLISH'
        events.append({
            'ts': timezone.localtime(a['answered_at']),
            'section': section,
            'domain': _domain(section, a['question__category']),
            'topic': (a['question__topic'] or '').strip(),
            'difficulty': (a['question__difficulty'] or 'MEDIUM').upper(),
            'correct': bool(a['is_correct']),
            'seconds': min(int(a['time_spent'] or 0), MAX_Q_SECONDS),
            'activity': 'module' if a['attempt__is_individual'] else 'test',
        })

    bank = (
        SavedBankQuestion.objects.filter(user=user).exclude(user_answer='')
        .values('is_correct', 'saved_at', 'question__subject', 'question__category',
                'question__topic', 'question__difficulty')
    )
    for b in bank:
        section = 'MATH' if b['question__subject'] == 'Matematika' else 'ENGLISH'
        events.append({
            'ts': timezone.localtime(b['saved_at']),
            'section': section,
            'domain': _domain(section, b['question__category']),
            'topic': (b['question__topic'] or '').strip(),
            'difficulty': (b['question__difficulty'] or 'medium').upper(),
            'correct': bool(b['is_correct']),
            'seconds': 0,
            'activity': 'bank',
        })

    events.sort(key=lambda e: e['ts'])
    return events


def _streak(days_with_activity, today):
    """Consecutive days ending today (or yesterday — today may simply not have started)."""
    day = today if today in days_with_activity else today - timedelta(days=1)
    n = 0
    while day in days_with_activity:
        n += 1
        day -= timedelta(days=1)
    return n


def _skills(events):
    by = defaultdict(lambda: {'attempts': 0, 'correct': 0})
    meta = {}
    for e in events:
        if not e['topic']:
            continue
        key = (e['section'], e['topic'].lower())
        by[key]['attempts'] += 1
        by[key]['correct'] += e['correct']
        meta[key] = (e['topic'], e['domain'])
    rows = []
    for key, v in by.items():
        topic, domain = meta[key]
        rows.append({
            'topic': topic, 'section': key[0], 'section_label': SECTION_LABEL[key[0]], 'domain': domain,
            'attempts': v['attempts'], 'correct': v['correct'], 'accuracy': _pct(v['correct'], v['attempts']),
        })
    return rows


def _sessions(events, since):
    """Sittings auto-detected from gaps between answers."""
    out, cur = [], None
    for e in events:
        if e['ts'] < since:
            continue
        if cur is None or (e['ts'] - cur['_last']).total_seconds() > SESSION_GAP:
            cur = {'start': e['ts'], '_last': e['ts'], 'questions': 0, 'correct': 0, 'seconds': 0, 'sections': set()}
            out.append(cur)
        cur['_last'] = e['ts']
        cur['questions'] += 1
        cur['correct'] += e['correct']
        cur['seconds'] += e['seconds']
        cur['sections'].add(SECTION_LABEL[e['section']])
    result = []
    for s in reversed(out):
        span = int((s['_last'] - s['start']).total_seconds())
        result.append({
            'start': s['start'].isoformat(),
            'questions': s['questions'],
            'correct': s['correct'],
            'accuracy': _pct(s['correct'], s['questions']),
            'seconds': max(s['seconds'], span),
            'sections': sorted(s['sections']),
        })
    return result[:12]


def build_analytics(user, range_key='30d'):
    days = RANGES.get(range_key, 30)
    now = timezone.localtime()
    today = now.date()
    all_events = _collect_events(user)

    if days is None:
        start_date = all_events[0]['ts'].date() if all_events else today
        events = all_events
    else:
        start_date = today - timedelta(days=days - 1)
        events = [e for e in all_events if e['ts'].date() >= start_date]

    attempted = len(events)
    correct = sum(e['correct'] for e in events)
    timed = [e for e in events if e['seconds'] > 0]
    total_seconds = sum(e['seconds'] for e in events)

    # ── domains (accuracy by topic area), with their skills nested ──
    skills = _skills(events)
    domains = {}
    for section, rows in DOMAINS.items():
        out = []
        for label, _keys in rows:
            evs = [e for e in events if e['section'] == section and e['domain'] == label]
            c = sum(e['correct'] for e in evs)
            out.append({
                'label': label, 'attempts': len(evs), 'correct': c, 'accuracy': _pct(c, len(evs)),
                'skills': sorted(
                    [s for s in skills if s['section'] == section and s['domain'] == label],
                    key=lambda s: (s['accuracy'], -s['attempts'])),
            })
        sec_events = [e for e in events if e['section'] == section]
        sec_correct = sum(e['correct'] for e in sec_events)
        domains[section] = {
            'label': SECTION_LABEL[section],
            'attempts': len(sec_events), 'correct': sec_correct, 'accuracy': _pct(sec_correct, len(sec_events)),
            'domains': out,
        }

    # ── weakest skills: enough attempts to mean something, lowest accuracy first ──
    ranked = [s for s in skills if s['attempts'] >= MIN_SKILL_ATTEMPTS]
    if len(ranked) < 3:
        ranked = list(skills)
    ranked.sort(key=lambda s: (s['accuracy'], -s['attempts']))
    lowest = ranked[:5]
    strongest = sorted([s for s in skills if s['attempts'] >= MIN_SKILL_ATTEMPTS and s['accuracy'] >= STRONG_ACCURACY],
                       key=lambda s: (-s['accuracy'], -s['attempts']))[:5]

    # ── difficulty: accuracy and time ──
    difficulty = {}
    for section in DOMAINS:
        rows = []
        for d in DIFFICULTIES:
            evs = [e for e in events if e['section'] == section and e['difficulty'] == d]
            c = sum(e['correct'] for e in evs)
            t = [e['seconds'] for e in evs if e['seconds'] > 0]
            rows.append({
                'difficulty': d, 'attempts': len(evs), 'correct': c, 'accuracy': _pct(c, len(evs)),
                'avg_seconds': round(sum(t) / len(t)) if t else None, 'total_seconds': sum(t),
            })
        difficulty[section] = rows

    # ── one row per day in the range ──
    blank_day = {'ENGLISH': 0, 'MATH': 0, 'q_ENGLISH': 0, 'q_MATH': 0, 'questions': 0, 'correct': 0}
    per_day = defaultdict(lambda: dict(blank_day))
    for e in events:
        d = per_day[e['ts'].date()]
        d[e['section']] += e['seconds']
        d['q_' + e['section']] += 1
        d['questions'] += 1
        d['correct'] += e['correct']
    daily = []
    span = (today - start_date).days + 1
    for i in range(span):
        day = start_date + timedelta(days=i)
        d = per_day.get(day, blank_day)
        daily.append({'date': day.isoformat(), 'english_seconds': d['ENGLISH'], 'math_seconds': d['MATH'],
                      'english_questions': d['q_ENGLISH'], 'math_questions': d['q_MATH'],
                      'questions': d['questions'], 'correct': d['correct']})
    active_days = sum(1 for d in daily if d['questions'])

    # ── where the time and the questions went ──
    by_subject = []
    for section in ('ENGLISH', 'MATH'):
        evs = [e for e in events if e['section'] == section]
        by_subject.append({'key': section, 'label': SECTION_LABEL[section], 'questions': len(evs),
                           'seconds': sum(e['seconds'] for e in evs)})
    by_activity = []
    for key in ('test', 'module', 'bank'):
        evs = [e for e in events if e['activity'] == key]
        by_activity.append({'key': key, 'label': ACTIVITY_LABEL[key], 'questions': len(evs),
                            'seconds': sum(e['seconds'] for e in evs)})

    hours = [0] * 24
    for e in events:
        hours[e['ts'].hour] += 1
    peak_hour = max(range(24), key=lambda h: hours[h]) if attempted else None

    # ── all-time activity, independent of the selected range ──
    heat_start = today - timedelta(days=HEATMAP_DAYS - 1)
    heat = defaultdict(int)
    weekday = [0] * 7
    all_days = set()
    for e in all_events:
        d = e['ts'].date()
        all_days.add(d)
        weekday[d.weekday()] += 1
        if d >= heat_start:
            heat[d.isoformat()] += 1
    weekday_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

    saved = (SavedQuestion.objects.filter(user=user).count()
             + SavedBankQuestion.objects.filter(user=user, is_bookmarked=True).count())

    return {
        'range': range_key if range_key in RANGES else '30d',
        'start_date': start_date.isoformat(),
        'end_date': today.isoformat(),
        'totals': {
            'attempted': attempted, 'correct': correct, 'wrong': attempted - correct,
            'accuracy': _pct(correct, attempted),
            'saved': saved,
            'streak': _streak(all_days, today),
            'total_seconds': total_seconds,
            'avg_seconds': round(sum(e['seconds'] for e in timed) / len(timed)) if timed else None,
            'active_days': active_days,
        },
        'lowest_skills': lowest,
        'strongest_skills': strongest,
        'sections': domains,
        'difficulty': difficulty,
        'daily': daily,
        'by_subject': by_subject,
        'by_activity': by_activity,
        'hours': hours,
        'peak_hour': peak_hour,
        'sessions': _sessions(all_events, now - timedelta(days=7)),
        'activity': {
            'start': heat_start.isoformat(), 'end': today.isoformat(), 'days': dict(heat),
            'all_time_questions': len(all_events),
            'most_active_weekday': weekday_names[max(range(7), key=lambda i: weekday[i])] if all_events else None,
        },
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def sat_analytics(request):
    """GET /api/sat/analytics/?range=7d|30d|90d|all"""
    return Response(build_analytics(request.user, request.query_params.get('range', '30d')))


# ── AI analysis ──────────────────────────────────────────────────────────────

def _ai_facts(data):
    """The compact, exact numbers the model is allowed to talk about."""
    def row(name_key, name, correct, attempts, accuracy, **extra):
        r = {name_key: name, 'result': f'{correct}/{attempts} ({accuracy}%)', 'correct': correct, 'attempts': attempts,
             'accuracy_pct': accuracy, **extra}
        if attempts < AI_RELIABLE_ATTEMPTS:
            r['too_few_attempts_to_judge'] = True
        return r

    def skill(s):
        return row('skill', s['topic'], s['correct'], s['attempts'], s['accuracy'], section=s['section_label'])

    t = data['totals']
    return {
        'period': {'from': data['start_date'], 'to': data['end_date']},
        'questions_attempted': t['attempted'], 'correct': t['correct'], 'wrong': t['wrong'],
        'accuracy_pct': t['accuracy'], 'avg_seconds_per_question': t['avg_seconds'],
        'study_streak_days': t['streak'], 'active_days': t['active_days'],
        'sections': {
            sec['label']: {
                'correct': sec['correct'], 'attempts': sec['attempts'], 'accuracy_pct': sec['accuracy'],
                'domains': [row('domain', d['label'], d['correct'], d['attempts'], d['accuracy'])
                            for d in sec['domains'] if d['attempts']],
            } for sec in data['sections'].values() if sec['attempts']
        },
        'weakest_skills': [skill(s) for s in data['lowest_skills']],
        'strongest_skills': [skill(s) for s in data['strongest_skills']],
        'by_difficulty': {
            SECTION_LABEL[sec]: [row('difficulty', r['difficulty'], r['correct'], r['attempts'], r['accuracy'],
                                     avg_seconds=r['avg_seconds'])
                                 for r in rows if r['attempts']]
            for sec, rows in data['difficulty'].items()
        },
    }
