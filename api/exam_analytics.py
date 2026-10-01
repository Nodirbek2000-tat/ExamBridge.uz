"""
IELTS / CEFR home-page analytics — one builder for both exams.

All counting happens in the database (values().annotate()), so the cost per
request is a fixed handful of queries however many answers a user has.

Objective skills (reading, listening) are counted per answered question:
  correct · wrong (an answer that is not right) · blank (left empty — in a
  timed test an empty answer scores zero, so it counts as attempted, but it is
  reported on its own because "ran out of time" and "chose wrong" need
  different fixes).
Writing / speaking come from the AI-scored responses.
"""
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Q
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

RANGES = {'7d': 7, '30d': 30, '90d': 90, 'all': None}
HEATMAP_DAYS = 153
MIN_TYPE_ATTEMPTS = 5          # a question type needs this many answers to be called weak
MISTAKE_SAMPLE = 15

QTYPE_LABEL = {
    'MCQ': 'Multiple choice', 'MULTI': 'Multiple select', 'TFNG': 'True / False / Not Given',
    'YNNG': 'Yes / No / Not Given', 'GAP': 'Gap filling', 'MATCH': 'Matching', 'MINFO': 'Matching information',
    'MFEAT': 'Matching features', 'MEND': 'Matching sentence endings', 'SHORT': 'Short answer',
    'SENT': 'Sentence completion', 'TABLE': 'Table completion', 'SUMM': 'Summary completion',
    'NOTE': 'Note / form completion', 'FLOW': 'Flow-chart completion', 'MAP': 'Map / diagram labelling',
    'PGAP': 'Gap in the text', 'TMATCH': 'Text matching',
}
WRITING_CRITERIA = [
    ('task_achievement', 'Task achievement', ('task_achievement', 'task_response')),
    ('coherence_cohesion', 'Coherence & cohesion', ('coherence_cohesion',)),
    ('lexical_resource', 'Lexical resource', ('lexical_resource',)),
    ('grammatical_range', 'Grammar', ('grammatical_range', 'grammatical_range_accuracy')),
]
SPEAKING_CRITERIA = [
    ('fluency_coherence', 'Fluency & coherence', ('fluency_coherence',)),
    ('lexical_resource', 'Lexical resource', ('lexical_resource',)),
    ('grammatical_range', 'Grammar', ('grammatical_range',)),
    ('pronunciation', 'Pronunciation', ('pronunciation',)),
]


def _models(exam):
    if exam == 'IELTS':
        from ielts.models import ReadingAnswer, ListeningAnswer
        return {
            'reading': (ReadingAnswer, 'question__passage__passage_number'),
            'listening': (ListeningAnswer, 'question__section__section_number'),
        }
    from cefr.models import CEFRReadingAnswer, CEFRListeningAnswer
    return {
        'reading': (CEFRReadingAnswer, 'question__passage__passage_number'),
        'listening': (CEFRListeningAnswer, 'question__section__section_number'),
    }


def _pct(c, n):
    return round(c * 100 / n) if n else None


def _band_of(value):
    """Criteria are stored either as a number or as {'band': n, ...}."""
    if isinstance(value, dict):
        value = value.get('band')
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _criteria_avg(rows, spec):
    out = []
    for key, label, aliases in spec:
        vals = []
        for crit in rows:
            if not isinstance(crit, dict):
                continue
            for a in aliases:
                if a in crit:
                    b = _band_of(crit[a])
                    if b is not None:
                        vals.append(b)
                    break
        out.append({'key': key, 'label': label, 'avg': round(sum(vals) / len(vals), 1) if vals else None, 'count': len(vals)})
    return out


def _streak(days, today):
    day = today if today in days else today - timedelta(days=1)
    n = 0
    while day in days:
        n += 1
        day -= timedelta(days=1)
    return n


def build_exam_analytics(user, exam, range_key='30d'):
    exam = exam.upper()
    days = RANGES.get(range_key, 30)
    tz = timezone.get_current_timezone()
    today = timezone.localdate()
    start = None if days is None else today - timedelta(days=days - 1)
    link_base = f'/app/{exam.lower()}/skills?tab='

    activity_days = set()
    heat = defaultdict(int)
    heat_start = today - timedelta(days=HEATMAP_DAYS - 1)
    daily_rows = defaultdict(lambda: defaultdict(int))
    skills = {}
    type_rows = []
    mistakes = []

    for skill, (model, part_field) in _models(exam).items():
        base = (model.objects.filter(attempt__user=user)
                .annotate(d=TruncDate(Coalesce('attempt__finished_at', 'attempt__started_at'), tzinfo=tz)))
        # all-time: calendar + streak, independent of the range
        for row in base.filter(d__gte=heat_start).values('d').annotate(n=Count('id')):
            heat[row['d'].isoformat()] += row['n']
        activity_days.update(base.values_list('d', flat=True).distinct())

        qs = base if start is None else base.filter(d__gte=start)
        # a blank answer is never counted as correct, even where old grading stored it as correct
        agg = dict(n=Count('id'), c=Count('id', filter=Q(is_correct=True) & ~Q(answer='')), b=Count('id', filter=Q(answer='')))

        by_type = []
        for r in qs.values('question__question_type').annotate(**agg).order_by('-n'):
            t = r['question__question_type'] or 'OTHER'
            row = {'type': t, 'label': QTYPE_LABEL.get(t, t.title()), 'skill': skill, 'attempts': r['n'],
                   'correct': r['c'], 'blank': r['b'], 'wrong': r['n'] - r['c'] - r['b'],
                   'accuracy': _pct(r['c'], r['n']), 'link': link_base + skill}
            by_type.append(row)
            type_rows.append(row)
        by_part = [{'part': r[part_field] or 0, 'attempts': r['n'], 'correct': r['c'], 'blank': r['b'],
                    'accuracy': _pct(r['c'], r['n'])}
                   for r in qs.values(part_field).annotate(**agg).order_by(part_field)]
        for r in qs.values('d').annotate(**agg):
            daily_rows[r['d']][skill] += r['n']
            daily_rows[r['d']]['correct'] += r['c']

        n = sum(t['attempts'] for t in by_type)
        c = sum(t['correct'] for t in by_type)
        b = sum(t['blank'] for t in by_type)
        skills[skill] = {'attempts': n, 'correct': c, 'blank': b, 'wrong': n - c - b, 'accuracy': _pct(c, n),
                         'by_type': by_type, 'by_part': by_part, 'link': link_base + skill}

        # real mistakes (a chosen answer that was wrong) — newest first
        for m in (qs.filter(is_correct=False).exclude(answer='')
                  .order_by('-d', '-id')
                  .values('answer', 'd', 'question__question_type', 'question__content',
                          'question__correct_answer', part_field)[:MISTAKE_SAMPLE]):
            content = ' '.join(str(m['question__content'] or '').split())
            mistakes.append({
                'skill': skill, 'type': m['question__question_type'],
                'type_label': QTYPE_LABEL.get(m['question__question_type'], m['question__question_type']),
                'part': m[part_field], 'date': m['d'].isoformat() if m['d'] else None,
                'question': content[:160] + ('…' if len(content) > 160 else ''),
                'your_answer': str(m['answer'])[:80], 'correct_answer': str(m['question__correct_answer']).split('|')[0][:80],
                'link': link_base + skill,
            })
    mistakes.sort(key=lambda m: m['date'] or '', reverse=True)

    # ── writing & speaking (AI-scored) ──
    from ielts.models import WritingResponse, SpeakingResponse

    def scored(qs, date_field='created_at'):
        qs = qs.filter(attempt__user=user)
        for d in qs.values_list(date_field, flat=True):
            ld = timezone.localtime(d).date()
            activity_days.add(ld)
            if ld >= heat_start:
                heat[ld.isoformat()] += 1
        if start is not None:
            qs = qs.filter(**{f'{date_field}__date__gte': start})
        return qs

    writing = None
    if exam == 'IELTS':
        rows = list(scored(WritingResponse.objects.all()).order_by('created_at')
                    .values('ai_band', 'ai_criteria', 'created_at', 'task__task_type'))
        bands = [float(r['ai_band']) for r in rows if r['ai_band']]
        writing = {
            'count': len(rows), 'scored': len(bands), 'avg_band': round(sum(bands) / len(bands), 1) if bands else None,
            'by_task': [{'task': t, 'count': len(v), 'avg_band': round(sum(v) / len(v), 1) if v else None}
                        for t in (1, 2) for v in [[float(r['ai_band']) for r in rows if r['ai_band'] and r['task__task_type'] == t]]],
            'criteria': _criteria_avg([r['ai_criteria'] for r in rows], WRITING_CRITERIA),
            'trend': [{'date': timezone.localtime(r['created_at']).date().isoformat(), 'band': float(r['ai_band']),
                       'task': r['task__task_type']} for r in rows if r['ai_band']][-20:],
            'link': link_base + 'writing',
        }
    rows = list(scored(SpeakingResponse.objects.filter(task__source=exam)).order_by('created_at')
                .values('ai_band', 'ai_criteria', 'created_at', 'task__part'))
    bands = [float(r['ai_band']) for r in rows if r['ai_band']]
    speaking = {
        'count': len(rows), 'scored': len(bands), 'avg_band': round(sum(bands) / len(bands), 1) if bands else None,
        'criteria': _criteria_avg([r['ai_criteria'] for r in rows], SPEAKING_CRITERIA),
        'trend': [{'date': timezone.localtime(r['created_at']).date().isoformat(), 'band': float(r['ai_band'])}
                  for r in rows if r['ai_band']][-20:],
        'link': link_base + 'speaking',
    }

    # ── finished tests and their scores ──
    history = []
    if exam == 'IELTS':
        from ielts.models import IELTSAttempt
        tq = IELTSAttempt.objects.filter(user=user, status='COMPLETED')
        if start is not None:
            tq = tq.filter(finished_at__date__gte=start)
        tests_completed = tq.count()
        for a in tq.filter(Q(reading_band__isnull=False) | Q(listening_band__isnull=False)).order_by('-finished_at')[:20]:
            for skill, band in (('reading', a.reading_band), ('listening', a.listening_band)):
                if band is not None:
                    history.append({'date': timezone.localtime(a.finished_at).date().isoformat() if a.finished_at else None,
                                    'skill': skill, 'score': float(band), 'unit': 'band'})
    else:
        from cefr.models import CEFRAttempt
        tq = CEFRAttempt.objects.filter(user=user, status='COMPLETED')
        if start is not None:
            tq = tq.filter(finished_at__date__gte=start)
        tests_completed = tq.count()
        for a in tq.filter(attempt_type__in=['READING', 'LISTENING']).order_by('-finished_at')[:20]:
            history.append({'date': timezone.localtime(a.finished_at).date().isoformat() if a.finished_at else None,
                            'skill': a.attempt_type.lower(), 'score': round(a.score_percent or 0),
                            'correct': a.correct_count, 'total': a.total_count, 'unit': 'percent'})
    history.reverse()

    # ── one row per day (questions answered) ──
    first_day = start or (min(daily_rows) if daily_rows else today)
    daily = []
    for i in range((today - first_day).days + 1):
        d = first_day + timedelta(days=i)
        r = daily_rows.get(d, {})
        daily.append({'date': d.isoformat(), 'reading': r.get('reading', 0), 'listening': r.get('listening', 0),
                      'correct': r.get('correct', 0)})

    n = sum(s['attempts'] for s in skills.values())
    c = sum(s['correct'] for s in skills.values())
    b = sum(s['blank'] for s in skills.values())
    weakest = sorted([t for t in type_rows if t['attempts'] >= MIN_TYPE_ATTEMPTS],
                     key=lambda t: (t['accuracy'], -t['attempts']))[:5]
    weekday = [0] * 7
    for iso, cnt in heat.items():
        y, m, d = map(int, iso.split('-'))
        from datetime import date as _date
        weekday[_date(y, m, d).weekday()] += cnt
    weekday_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

    return {
        'exam': exam,
        'range': range_key if range_key in RANGES else '30d',
        'start_date': first_day.isoformat(), 'end_date': today.isoformat(),
        'totals': {
            'answered': n, 'correct': c, 'blank': b, 'wrong': n - c - b, 'accuracy': _pct(c, n),
            'tests_completed': tests_completed,
            'writing_count': writing['count'] if writing else 0,
            'writing_band': writing['avg_band'] if writing else None,
            'speaking_count': speaking['count'], 'speaking_band': speaking['avg_band'],
            'streak': _streak(activity_days, today),
            'active_days': sum(1 for d in daily if d['reading'] or d['listening']),
        },
        'skills': skills,
        'weakest_types': weakest,
        'writing': writing,
        'speaking': speaking,
        'history': history,
        'mistakes': mistakes[:MISTAKE_SAMPLE],
        'daily': daily,
        'activity': {
            'start': heat_start.isoformat(), 'end': today.isoformat(), 'days': dict(heat),
            'most_active_weekday': weekday_names[max(range(7), key=lambda i: weekday[i])] if heat else None,
            'all_time_questions': sum(heat.values()),
        },
    }


def ai_facts(data):
    """Exact numbers (and real mistakes) the AI may use for an IELTS / CEFR analysis."""
    def row(r, name):
        out = {name: r['label'] if 'label' in r else r[name], 'result': f"{r['correct']}/{r['attempts']} ({r['accuracy']}%)",
               'correct': r['correct'], 'attempts': r['attempts'], 'accuracy_pct': r['accuracy'], 'left_blank': r.get('blank', 0)}
        if r['attempts'] < MIN_TYPE_ATTEMPTS:
            out['too_few_attempts_to_judge'] = True
        return out

    t = data['totals']
    facts = {
        'exam': data['exam'], 'period': {'from': data['start_date'], 'to': data['end_date']},
        'overall_result': f"{t['correct']}/{t['answered']} ({t['accuracy']}%)" if t['answered'] else None,
        'questions_answered': t['answered'], 'correct': t['correct'], 'wrong_answers': t['wrong'],
        'left_blank': t['blank'], 'accuracy_pct': t['accuracy'], 'tests_completed': t['tests_completed'],
        'study_streak_days': t['streak'],
        'skills': {
            skill: {
                'result': f"{s['correct']}/{s['attempts']} ({s['accuracy']}%)",
                'correct': s['correct'], 'attempts': s['attempts'], 'accuracy_pct': s['accuracy'], 'left_blank': s['blank'],
                'by_question_type': [row(x, 'type') for x in s['by_type']],
                'by_part': [{'part': p['part'], 'result': f"{p['correct']}/{p['attempts']} ({p['accuracy']}%)",
                             'correct': p['correct'], 'attempts': p['attempts'], 'accuracy_pct': p['accuracy']}
                            for p in s['by_part']],
            } for skill, s in data['skills'].items() if s['attempts']
        },
        'weakest_question_types': [dict(row(x, 'type'), skill=x['skill']) for x in data['weakest_types']],
        'recent_mistakes': [{'skill': m['skill'], 'question_type': m['type_label'], 'part': m['part'],
                             'question': m['question'], 'student_answer': m['your_answer'], 'correct_answer': m['correct_answer']}
                            for m in data['mistakes']],
    }
    for name in ('writing', 'speaking'):
        w = data.get(name)
        if w and w['count']:
            facts[name] = {'responses': w['count'], 'avg_band': w['avg_band'],
                           'criteria_avg_band': {c['label']: c['avg'] for c in w['criteria'] if c['avg'] is not None}}
            if w.get('trend'):
                facts[name]['first_band'] = w['trend'][0]['band']
                facts[name]['latest_band'] = w['trend'][-1]['band']
    if data['history']:
        facts['test_scores'] = [{'date': h['date'], 'skill': h['skill'], 'score': h['score'], 'unit': h['unit']} for h in data['history']]
    return facts
