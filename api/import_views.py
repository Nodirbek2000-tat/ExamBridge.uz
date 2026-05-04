"""
JSON Import endpoints for SAT, IELTS, CEFR questions
"""
import json
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.parsers import MultiPartParser, JSONParser
from rest_framework.response import Response
from django.db import transaction

# ── Question type aliases — normalize human-friendly names to DB codes ─────────
_QT_ALIASES = {
    'SENTENCE': 'SENT',
    'SENTENCE_COMP': 'SENT',
    'SENTENCE_COMPLETION': 'SENT',
    'SUMMARY': 'SUMM',
    'SUMMARY_FILL': 'SUMM',
    'SUMMARY_COMPLETION': 'SUMM',
    'NOTES': 'NOTE',
    'NOTES_FILL': 'NOTE',
    'NOTE_COMPLETION': 'NOTE',
    'FLOW_FILL': 'FLOW',
    'FLOWCHART': 'FLOW',
    'FLOWCHART_COMPLETION': 'FLOW',
    'DIAGRAM': 'MAP',
    'DIAGRAM_LABELING': 'MAP',
    'HEADING': 'MATCH',
    'HEADINGS': 'MATCH',
    'HEADING_MATCH': 'MATCH',
    'HEADINGS_MATCHING': 'MATCH',
    'MATCHING_FEATURES': 'MFEAT',
    'M.FEAT': 'MFEAT',
    'MFEAT': 'MFEAT',
    'MATCHING_SENTENCE': 'MEND',
    'MATCHING_SENTENCE_ENDINGS': 'MEND',
    'M.END': 'MEND',
    'MEND': 'MEND',
    'MATCHING_INFO': 'MINFO',
    'MATCHING_INFORMATION': 'MINFO',
    'M.INFO': 'MINFO',
    'MINFO': 'MINFO',
    'TABLE_COMPLETION': 'TABLE',
    'SHORT_ANSWER': 'SHORT',
    'TRUE_FALSE': 'TFNG',
    'TRUE_FALSE_NOT_GIVEN': 'TFNG',
    'YES_NO': 'YNNG',
    'YES_NO_NOT_GIVEN': 'YNNG',
    'MULTIPLE_CHOICE': 'MCQ',
    'MULTIPLE': 'MULTI',
    'MULTI_SELECT': 'MULTI',
    'GAP_FILL': 'GAP',
}

def _normalize_qt(qt):
    """Normalize question type codes — accept human-friendly aliases."""
    if not qt:
        return 'MCQ'
    return _QT_ALIASES.get(qt.upper(), qt.upper())


# ── SAT IMPORT ───────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAdminUser])
def import_sat_questions(request):
    """
    Import SAT questions into an existing test module.

    POST body:
    {
        "test_id": 1,
        "section": "MATH",       // MATH or ENGLISH
        "module_number": 1,       // 1 or 2
        "questions": [...]
    }
    """
    try:
        data = request.data if isinstance(request.data, dict) else json.loads(request.body)
        file = request.FILES.get('file')
        if file:
            data = json.load(file)

        test_id = data.get('test_id')
        section_type = data.get('section', 'MATH').upper()
        module_number = data.get('module_number', 1)
        questions_data = data.get('questions', [])

        if not questions_data:
            return Response({'error': 'No questions provided.'}, status=400)

        from tests_app.models import Test, TestSection, Module, Question, Choice

        test = Test.objects.get(id=test_id)
        section, _ = TestSection.objects.get_or_create(test=test, section_type=section_type)
        module, _ = Module.objects.get_or_create(
            section=section,
            module_number=module_number,
            defaults={'time_limit': 35 if section_type == 'MATH' else 32}
        )

        created = 0
        with transaction.atomic():
            for q_data in questions_data:
                q, created_new = Question.objects.update_or_create(
                    module=module,
                    number=q_data['number'],
                    defaults={
                        'question_type': q_data.get('question_type', 'MCQ'),
                        'content': q_data.get('content') or '',
                        'math_equation': q_data.get('math_equation') or '',
                        'passage': q_data.get('passage') or '',
                        'correct_answer': q_data.get('correct_answer', ''),
                        'explanation': q_data.get('explanation') or '',
                        'difficulty': q_data.get('difficulty', 'MEDIUM').upper(),
                    }
                )
                if created_new:
                    created += 1

                # Create choices for MCQ
                for c_data in q_data.get('choices', []):
                    Choice.objects.update_or_create(
                        question=q,
                        option=c_data['option'],
                        defaults={'text': c_data['text']}
                    )

        return Response({'created': created, 'total': len(questions_data), 'status': 'ok'})

    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([IsAdminUser])
def import_sat_practice(request):
    """
    Import practice questions grouped by topic.

    POST body:
    {
        "subject": "Math",          // "Math" or "English"
        "category": "algebra",
        "topics": [
            {
                "topic": "Linear Equations",
                "questions": [
                    {
                        "question_type": "MCQ",
                        "content": "...",
                        "choice_a": "...", "choice_b": "...", "choice_c": "...", "choice_d": "...",
                        "correct_answer": "A",
                        "difficulty": "easy",
                        "explanation": "..."
                    }
                ]
            }
        ]
    }
    """
    try:
        data = request.data if isinstance(request.data, dict) else json.loads(request.body)
        file = request.FILES.get('file')
        if file:
            data = json.load(file)

        subject_raw = data.get('subject', 'Math')
        subject_map = {
            'math': 'Matematika', 'matematik': 'Matematika', 'matematika': 'Matematika',
            'english': 'Ingliz tili', 'ingliz tili': 'Ingliz tili',
            'reading': 'Reading',
        }
        subject = subject_map.get(subject_raw.lower(), subject_raw)
        category = data.get('category', '')
        topics = data.get('topics', [])

        if not topics:
            return Response({'error': 'No topics provided.'}, status=400)

        from tests_app.models import BankQuestion
        created_total = 0

        def _extract_choices(q_data):
            """Support both old (choice_a/b/c/d) and new (choices array) formats."""
            if 'choices' in q_data and isinstance(q_data['choices'], list):
                mapping = {c['option'].upper(): c.get('text', '') for c in q_data['choices'] if 'option' in c}
                return (mapping.get('A', ''), mapping.get('B', ''), mapping.get('C', ''), mapping.get('D', ''))
            return (
                q_data.get('choice_a', '') or q_data.get('choiceA', ''),
                q_data.get('choice_b', '') or q_data.get('choiceB', ''),
                q_data.get('choice_c', '') or q_data.get('choiceC', ''),
                q_data.get('choice_d', '') or q_data.get('choiceD', ''),
            )

        with transaction.atomic():
            for topic_data in topics:
                topic_name = topic_data.get('topic', '')
                questions = topic_data.get('questions', [])
                for q_data in questions:
                    ca, cb, cc, cd = _extract_choices(q_data)
                    BankQuestion.objects.create(
                        subject=subject,
                        category=category,
                        question_type=q_data.get('question_type', 'MCQ').upper(),
                        topic=topic_name,
                        content=q_data.get('content') or '',
                        math_equation=q_data.get('math_equation', ''),
                        passage=q_data.get('passage', ''),
                        table_data=q_data.get('table_data') or None,
                        difficulty=q_data.get('difficulty', 'medium').lower(),
                        choice_a=ca,
                        choice_b=cb,
                        choice_c=cc,
                        choice_d=cd,
                        correct_answer=q_data.get('correct_answer', ''),
                        explanation=q_data.get('explanation') or '',
                        year=q_data.get('year') or None,
                        source=q_data.get('source', ''),
                    )
                    created_total += 1

        return Response({'created': created_total, 'topics': len(topics), 'status': 'ok'})

    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([IsAdminUser])
def import_sat_mock(request):
    """
    Import a full SAT mock test with adaptive Module 2.

    POST body:
    {
        "name": "SAT March 2024",
        "year": 2024,
        "month": 3,
        "form": "A",
        "is_international": false,
        "english_m1": [...27 questions...],
        "english_m2_easy": [...27 questions...],
        "english_m2_hard": [...27 questions...],
        "math_m1": [...22 questions...],
        "math_m2_easy": [...22 questions...],
        "math_m2_hard": [...22 questions...]
    }

    Each question: { number, question_type, content, passage, correct_answer,
                     difficulty, explanation, choices: [{option, text}] }
    """
    try:
        data = request.data if isinstance(request.data, dict) else json.loads(request.body)
        file = request.FILES.get('file')
        if file:
            data = json.load(file)

        from tests_app.models import Test, TestSection, Module, Question, Choice

        # Parse year/month from name if not given
        year = data.get('year')
        month = data.get('month')
        form = data.get('form', 'A')

        if not year or not month:
            return Response({'error': 'year and month are required.'}, status=400)

        # Determine test_mode from JSON (default FULL for adaptive tests)
        raw_mode = data.get('test_mode', 'FULL').upper()
        test_mode = raw_mode if raw_mode in ('FULL', 'INDIVIDUAL') else 'FULL'

        test, created = Test.objects.get_or_create(
            test_type='SAT',
            year=int(year),
            month=int(month),
            form=form,
            is_international=data.get('is_international', False),
            defaults={
                'is_active': True,
                'is_premium': data.get('is_premium', False),
                'test_mode': test_mode,
            }
        )
        # If test already existed, update test_mode if explicitly provided
        if not created and 'test_mode' in data:
            test.test_mode = test_mode
            test.save(update_fields=['test_mode'])

        def _import_module(section_obj, module_num, variant, questions_data):
            module_obj, _ = Module.objects.get_or_create(
                section=section_obj,
                module_number=module_num,
                difficulty_variant=variant,
                defaults={'time_limit': 35 if section_obj.section_type == 'MATH' else 32}
            )
            for q_data in questions_data:
                q, _ = Question.objects.update_or_create(
                    module=module_obj,
                    number=q_data['number'],
                    defaults={
                        'question_type': q_data.get('question_type', 'MCQ').upper(),
                        'content': q_data.get('content') or '',
                        'math_equation': q_data.get('math_equation') or '',
                        'passage': q_data.get('passage') or '',
                        'table_data': q_data.get('table_data'),
                        'correct_answer': q_data.get('correct_answer', ''),
                        'explanation': q_data.get('explanation') or '',
                        'difficulty': q_data.get('difficulty', 'MEDIUM').upper(),
                        'category': q_data.get('category', '').lower().replace(' ', '_'),
                        'topic': q_data.get('topic', ''),
                    }
                )
                for c_data in q_data.get('choices', []):
                    Choice.objects.update_or_create(
                        question=q, option=c_data['option'],
                        defaults={'text': c_data['text']}
                    )
            return len(questions_data)

        total = 0
        with transaction.atomic():
            eng_section, _ = TestSection.objects.get_or_create(test=test, section_type='ENGLISH')
            math_section, _ = TestSection.objects.get_or_create(test=test, section_type='MATH')

            if data.get('english_m1'):
                total += _import_module(eng_section, 1, 'STANDARD', data['english_m1'])

            if test_mode == 'INDIVIDUAL':
                # INDIVIDUAL mode: single standard M2
                if data.get('english_m2'):
                    total += _import_module(eng_section, 2, 'STANDARD', data['english_m2'])
            else:
                # FULL mode: adaptive easy/medium/hard M2
                if data.get('english_m2_easy'):
                    total += _import_module(eng_section, 2, 'EASY', data['english_m2_easy'])
                if data.get('english_m2_medium'):
                    total += _import_module(eng_section, 2, 'MEDIUM', data['english_m2_medium'])
                if data.get('english_m2_hard'):
                    total += _import_module(eng_section, 2, 'HARD', data['english_m2_hard'])

            if data.get('math_m1'):
                total += _import_module(math_section, 1, 'STANDARD', data['math_m1'])

            if test_mode == 'INDIVIDUAL':
                if data.get('math_m2'):
                    total += _import_module(math_section, 2, 'STANDARD', data['math_m2'])
            else:
                if data.get('math_m2_easy'):
                    total += _import_module(math_section, 2, 'EASY', data['math_m2_easy'])
                if data.get('math_m2_medium'):
                    total += _import_module(math_section, 2, 'MEDIUM', data['math_m2_medium'])
                if data.get('math_m2_hard'):
                    total += _import_module(math_section, 2, 'HARD', data['math_m2_hard'])

        return Response({
            'test_id': test.id,
            'test_name': test.display_name,
            'test_mode': test.test_mode,
            'created_test': created,
            'total_questions': total,
            'status': 'ok',
        })

    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser, JSONParser])
def admin_sat_question_detail(request, pk):
    """View, edit, or delete a single SAT test question."""
    from tests_app.models import Question, Choice
    from django.shortcuts import get_object_or_404
    q = get_object_or_404(Question, id=pk)

    if request.method == 'DELETE':
        q.delete()
        return Response({'deleted': True})

    if request.method == 'PUT':
        data = request.data
        for field in ['content', 'passage', 'correct_answer', 'explanation', 'difficulty', 'question_type']:
            if field in data:
                setattr(q, field, data[field])
        if 'image' in request.FILES:
            q.image = request.FILES['image']
        elif data.get('remove_image') in (True, 'true', '1'):
            q.image = None
        q.save()
        choices_raw = data.get('choices', [])
        if isinstance(choices_raw, str):
            import json as _json
            try:
                choices_raw = _json.loads(choices_raw)
            except Exception:
                choices_raw = []
        for c_data in choices_raw:
            Choice.objects.update_or_create(
                question=q, option=c_data['option'],
                defaults={'text': c_data.get('text', '')}
            )
        # Handle per-choice images sent as choice_image_A, choice_image_B, etc.
        for letter in ['A', 'B', 'C', 'D']:
            file_key = f'choice_image_{letter}'
            if file_key in request.FILES:
                choice_obj, _ = Choice.objects.get_or_create(question=q, option=letter, defaults={'text': ''})
                choice_obj.image = request.FILES[file_key]
                choice_obj.save()
        def img_url(f):
            return request.build_absolute_uri(f.url) if f else None
        return Response({
            'updated': True,
            'image': img_url(q.image),
            'choices': [{'option': c.option, 'text': c.text, 'image': img_url(c.image)} for c in q.choices.all()],
        })

    return Response({
        'id': q.id,
        'number': q.number,
        'question_type': q.question_type,
        'difficulty': q.difficulty,
        'content': q.content,
        'passage': q.passage,
        'correct_answer': q.correct_answer,
        'explanation': q.explanation,
        'image': q.image.url if q.image else None,
        'section': q.module.section.section_type,
        'module': q.module.module_number,
        'difficulty_variant': q.module.difficulty_variant,
        'choices': [{'option': c.option, 'text': c.text, 'image': c.image.url if c.image else None} for c in q.choices.all()],
    })


@api_view(['POST', 'DELETE'])
@permission_classes([IsAdminUser])
def admin_sat_question_choice_image(request, pk, letter):
    """Upload or delete image for a specific choice (A/B/C/D) of a full-length test question."""
    from tests_app.models import Question, Choice
    from django.shortcuts import get_object_or_404
    q = get_object_or_404(Question, id=pk)
    letter = letter.upper()
    if letter not in ['A', 'B', 'C', 'D']:
        return Response({'error': 'Invalid letter. Use A/B/C/D.'}, status=400)
    if request.method == 'DELETE':
        try:
            choice_obj = Choice.objects.get(question=q, option=letter)
            if choice_obj.image:
                choice_obj.image.delete(save=False)
                choice_obj.image = None
                choice_obj.save()
        except Choice.DoesNotExist:
            pass
        return Response({'url': None, 'option': letter})
    f = request.FILES.get('image')
    if not f:
        return Response({'error': 'No image file provided.'}, status=400)
    choice_obj, _ = Choice.objects.get_or_create(question=q, option=letter, defaults={'text': ''})
    choice_obj.image = f
    choice_obj.save()
    url = request.build_absolute_uri(choice_obj.image.url) if choice_obj.image else None
    return Response({'url': url, 'option': letter})


@api_view(['POST'])
@permission_classes([IsAdminUser])
def import_sat_test(request):
    """Create a new SAT test with sections and modules."""
    try:
        data = request.data
        from tests_app.models import Test
        test, created = Test.objects.get_or_create(
            test_type=data.get('test_type', 'SAT'),
            year=data['year'],
            month=data['month'],
            form=data.get('form', 'A'),
            is_international=data.get('is_international', False),
            defaults={
                'is_active': data.get('is_active', True),
                'is_premium': data.get('is_premium', False),
            }
        )
        return Response({
            'id': test.id,
            'display_name': test.display_name,
            'created': created,
        })
    except Exception as e:
        return Response({'error': str(e)}, status=400)


# ── IELTS IMPORT ─────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAdminUser])
def import_ielts_reading(request):
    """
    Import IELTS Reading — supports single passage OR full mock (multi-part).

    SINGLE PASSAGE (practice):
    {
        "title": "Practice — The History of Coffee",
        "content": "Full passage text...",
        "passage_number": 1,
        "is_standalone": true,
        "difficulty": "MEDIUM",
        "is_premium": false,
        "questions": [
            {"number": 1, "question_type": "TFNG", "content": "...", "correct_answer": "TRUE", "explanation": "..."},
            {"number": 2, "question_type": "MCQ", "content": "...", "correct_answer": "A",
             "choices": [{"option": "A", "text": "..."}, {"option": "B", "text": "..."}]}
        ]
    }

    FULL MOCK (1–3 parts):
    {
        "title": "IELTS Reading Mock Test 1",
        "test_type": "FULL_MOCK",
        "difficulty": "MEDIUM",
        "is_premium": false,
        "parts": [
            {
                "passage_number": 1,
                "title": "Part 1 — The History of Coffee",
                "content": "Full passage text...",
                "questions": [...]
            },
            {
                "passage_number": 2,
                "title": "Part 2 — Solar Energy",
                "content": "Full passage text...",
                "questions": [...]
            }
        ]
    }
    NOTE: time_limit is auto-calculated: 1 part=20min, 2 parts=40min, 3 parts=60min
    """
    try:
        data = request.data
        file = request.FILES.get('file')
        if file:
            data = json.load(file)

        from ielts.models import IELTSTest, ReadingPassage, ReadingQuestion, ReadingChoice

        # Multi-part import (has "parts" key)
        if 'parts' in data:
            parts_data = data['parts']
            if not parts_data:
                return Response({'error': 'No parts provided.'}, status=400)

            with transaction.atomic():
                test = IELTSTest.objects.create(
                    title=data['title'],
                    test_type=data.get('test_type', 'FULL_MOCK'),
                    description=data.get('description', ''),
                    is_premium=data.get('is_premium', False),
                )
                created_passages = []
                for part in parts_data:
                    time_limit = len(parts_data) * 20 // len(parts_data)  # 20 min per part
                    passage = ReadingPassage.objects.create(
                        test=test,
                        title=part['title'],
                        content=part['content'],
                        passage_number=part.get('passage_number', 1),
                        time_limit=20,
                        difficulty=data.get('difficulty', 'MEDIUM'),
                        is_standalone=False,
                        is_premium=data.get('is_premium', False),
                    )
                    for q_data in part.get('questions', []):
                        q = ReadingQuestion.objects.create(
                            passage=passage,
                            number=q_data['number'],
                            question_type=_normalize_qt(q_data.get('question_type', 'MCQ')),
                            content=q_data.get('content') or '',
                            correct_answer=q_data.get('correct_answer', ''),
                            explanation=q_data.get('explanation') or '',
                            group_instruction=q_data.get('group_instruction', ''),
                            max_selections=q_data.get('max_selections', 1),
                            word_bank=q_data.get('word_bank', []),
                            answer_review=q_data.get('answer_review', ''),
                        )
                        for c in q_data.get('choices', []):
                            ReadingChoice.objects.create(question=q, option=c['option'], text=c['text'])
                    created_passages.append({'id': passage.id, 'title': passage.title, 'questions': passage.questions.count()})

            return Response({
                'test_id': test.id,
                'title': test.title,
                'part_count': len(parts_data),
                'passages': created_passages,
            })

        # Single passage import (backward compatible)
        with transaction.atomic():
            passage = ReadingPassage.objects.create(
                title=data['title'],
                content=data['content'],
                passage_number=data.get('passage_number', 1),
                time_limit=data.get('time_limit', 20),
                difficulty=data.get('difficulty', 'MEDIUM'),
                is_standalone=data.get('is_standalone', True),
                is_premium=data.get('is_premium', False),
            )
            for q_data in data.get('questions', []):
                q = ReadingQuestion.objects.create(
                    passage=passage,
                    number=q_data['number'],
                    question_type=_normalize_qt(q_data.get('question_type', 'MCQ')),
                    content=q_data.get('content') or '',
                    correct_answer=q_data.get('correct_answer', ''),
                    explanation=q_data.get('explanation') or '',
                    group_instruction=q_data.get('group_instruction', ''),
                    max_selections=q_data.get('max_selections', 1),
                    word_bank=q_data.get('word_bank', []),
                    answer_review=q_data.get('answer_review', ''),
                )
                for c in q_data.get('choices', []):
                    ReadingChoice.objects.create(question=q, option=c['option'], text=c['text'])

        return Response({'id': passage.id, 'title': passage.title, 'questions': passage.questions.count()})

    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([IsAdminUser])
def import_ielts_listening(request):
    """
    Import IELTS Listening — single section OR full mock (multi-section).
    Audio files are uploaded separately via /api/admin/ielts/listening/<id>/audio/

    SINGLE SECTION (practice):
    {
        "title": "Section 1 — Hotel Booking",
        "section_number": 1,
        "is_standalone": true,
        "difficulty": "EASY",
        "is_premium": false,
        "transcript": "Optional transcript text...",
        "questions": [
            {"number": 1, "question_type": "GAP", "content": "The caller's name is ___.", "correct_answer": "John Smith"},
            {"number": 2, "question_type": "MCQ", "content": "What floor is the room on?",
             "correct_answer": "B",
             "choices": [{"option": "A", "text": "3rd"}, {"option": "B", "text": "5th"}, {"option": "C", "text": "7th"}]}
        ]
    }

    FULL MOCK (1–4 sections):
    {
        "title": "IELTS Listening Mock Test 1",
        "difficulty": "MEDIUM",
        "is_premium": false,
        "sections": [
            {
                "section_number": 1,
                "title": "Section 1 — Hotel Booking",
                "transcript": "...",
                "questions": [...]
            },
            {
                "section_number": 2,
                "title": "Section 2 — Tour Guide",
                "transcript": "...",
                "questions": [...]
            }
        ]
    }
    NOTE: Upload audio after creation via PATCH /api/admin/ielts/listening/<id>/audio/
    """
    try:
        data = request.data
        file_json = request.FILES.get('file')
        if file_json:
            data = json.load(file_json)

        from ielts.models import IELTSTest, ListeningSection, ListeningQuestion, ListeningChoice

        # Multi-section import
        if 'sections' in data:
            sections_data = data['sections']
            if not sections_data:
                return Response({'error': 'No sections provided.'}, status=400)

            with transaction.atomic():
                test = IELTSTest.objects.create(
                    title=data['title'],
                    test_type='FULL_MOCK',
                    description=data.get('description', ''),
                    is_premium=data.get('is_premium', False),
                )
                created_sections = []
                for sec in sections_data:
                    section = ListeningSection.objects.create(
                        test=test,
                        title=sec['title'],
                        section_number=sec.get('section_number', 1),
                        transcript=sec.get('transcript', ''),
                        difficulty=data.get('difficulty', 'MEDIUM'),
                        is_standalone=False,
                        is_premium=data.get('is_premium', False),
                    )
                    for q_data in sec.get('questions', []):
                        q = ListeningQuestion.objects.create(
                            section=section,
                            number=q_data['number'],
                            question_type=_normalize_qt(q_data.get('question_type', 'GAP')),
                            content=q_data.get('content') or '',
                            correct_answer=q_data.get('correct_answer', ''),
                            explanation=q_data.get('explanation') or '',
                            group_instruction=q_data.get('group_instruction', ''),
                            max_selections=q_data.get('max_selections', 1),
                            word_bank=q_data.get('word_bank', []),
                            answer_review=q_data.get('answer_review', ''),
                        )
                        for c in q_data.get('choices', []):
                            ListeningChoice.objects.create(question=q, option=c['option'], text=c['text'])
                    created_sections.append({'id': section.id, 'title': section.title, 'questions': section.questions.count()})

            return Response({
                'test_id': test.id,
                'title': test.title,
                'section_count': len(sections_data),
                'sections': created_sections,
                'note': 'Upload audio for each section via /api/admin/ielts/listening/<id>/audio/',
            })

        # Single section import (+ optional audio file)
        audio_file = request.FILES.get('audio')
        with transaction.atomic():
            section = ListeningSection(
                title=data['title'],
                section_number=data.get('section_number', 1),
                transcript=data.get('transcript', ''),
                difficulty=data.get('difficulty', 'MEDIUM'),
                is_standalone=data.get('is_standalone', True),
                is_premium=data.get('is_premium', False),
            )
            if audio_file:
                section.audio_file = audio_file
            section.save()

            for q_data in data.get('questions', []):
                q = ListeningQuestion.objects.create(
                    section=section,
                    number=q_data['number'],
                    question_type=_normalize_qt(q_data.get('question_type', 'GAP')),
                    content=q_data.get('content') or '',
                    correct_answer=q_data.get('correct_answer', ''),
                    explanation=q_data.get('explanation') or '',
                    group_instruction=q_data.get('group_instruction', ''),
                    max_selections=q_data.get('max_selections', 1),
                    word_bank=q_data.get('word_bank', []),
                    answer_review=q_data.get('answer_review', ''),
                )
                for c in q_data.get('choices', []):
                    ListeningChoice.objects.create(question=q, option=c['option'], text=c['text'])

        return Response({'id': section.id, 'title': section.title, 'questions': section.questions.count()})

    except Exception as e:
        return Response({'error': str(e)}, status=400)


@api_view(['POST'])
@permission_classes([IsAdminUser])
def import_ielts_speaking(request):
    from ielts.models import SpeakingTask

    raw = request.data
    file = request.FILES.get('file')
    if file:
        data_from_file = json.load(file)
        # Support { "tasks": [...] } wrapper
        if isinstance(data_from_file, dict) and 'tasks' in data_from_file:
            items = data_from_file['tasks']
        elif isinstance(data_from_file, list):
            items = data_from_file
        else:
            items = [data_from_file]
    elif isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict) and 'tasks' in raw:
        items = raw['tasks']
    else:
        items = [raw]

    created = []
    for data in items:
        if data.get('test_type') == 'MOCK' or 'parts' in data:
            task = SpeakingTask.objects.create(
                title=data['title'],
                test_type='MOCK',
                part=0,
                topic=data.get('topic', ''),
                prompt='',
                questions=[],
                bullet_points=[],
                follow_up='',
                parts_data=data.get('parts', []),
                is_premium=data.get('is_premium', False),
            )
            created.append({'id': task.id, 'test_type': 'MOCK', 'parts': len(data.get('parts', []))})
        else:
            task = SpeakingTask.objects.create(
                title=data['title'],
                test_type='PART',
                part=data.get('part', 1),
                topic=data.get('topic', ''),
                prompt=data.get('prompt', data.get('cue_card', '')),
                questions=data.get('questions', []),
                bullet_points=data.get('bullet_points', []),
                follow_up=data.get('follow_up', data.get('follow_up_questions', [''])[0] if data.get('follow_up_questions') else ''),
                parts_data=[],
                is_premium=data.get('is_premium', False),
            )
            created.append({'id': task.id, 'part': task.part})

    if len(created) == 1:
        return Response(created[0], status=201)
    return Response({'created': len(created), 'items': created}, status=201)


@api_view(['POST'])
@permission_classes([IsAdminUser])
def import_ielts_writing(request):
    """Import IELTS Writing tasks."""
    try:
        data = request.data
        file = request.FILES.get('file')
        if file:
            items = json.load(file)
        else:
            items = data if isinstance(data, list) else [data]

        from ielts.models import WritingTask

        created = 0
        for item in items:
            WritingTask.objects.create(
                title=item['title'],
                task_type=item.get('task_type', 2),
                test_type=item.get('test_type', 'ACADEMIC'),
                difficulty=item.get('difficulty', 'MEDIUM').upper(),
                prompt=item['prompt'],
                recommendations=item.get('recommendations', []),
                min_words=item.get('min_words', 250),
                time_limit=item.get('time_limit', 40),
                sample_answer=item.get('sample_answer', ''),
                is_premium=item.get('is_premium', False),
            )
            created += 1

        return Response({'created': created})

    except Exception as e:
        return Response({'error': str(e)}, status=400)


# ── CEFR IMPORT ──────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAdminUser])
def import_cefr_test(request):
    """
    Import CEFR content. Supports three modes based on "type" field:

    TYPE: grammar (default) — Grammar/Vocabulary test
    {
        "type": "grammar",
        "title": "B2 Grammar Test 1",
        "level": "B2",
        "test_type": "GRAMMAR",
        "time_limit": 45,
        "is_premium": false,
        "questions": [
            {
                "number": 1,
                "question_type": "MCQ",   // MCQ|GAP|TF|MATCH|ERROR|WORD
                "content": "She ___ to Paris last year.",
                "passage": "",
                "correct_answer": "A",
                "explanation": "...",
                "group_instruction": "",
                "choices": [{"option": "A", "text": "went"}, {"option": "B", "text": "goes"}]
            }
        ]
    }

    TYPE: reading — Reading passage with questions
    {
        "type": "reading",
        "level": "B2",
        "title": "B2 Reading Practice 1",
        "passage": {
            "title": "The Impact of Social Media",
            "content": "Social media has...",
            "passage_number": 1,
            "is_standalone": true
        },
        "questions": [
            {
                "number": 1,
                "question_type": "TFNG",  // TFNG|YNNG|MCQ|MULTI|GAP|MATCH|SHORT
                "content": "...",
                "correct_answer": "TRUE",
                "explanation": "...",
                "group_instruction": "Questions 1-5: TRUE, FALSE or NOT GIVEN?",
                "max_selections": 1,
                "choices": []
            }
        ]
    }

    TYPE: listening — Listening section with questions
    {
        "type": "listening",
        "level": "B1",
        "title": "B1 Listening Practice 1",
        "section": {
            "title": "A phone conversation",
            "section_number": 1,
            "audio_url": "https://...",
            "transcript": "Hello [1], how are you?",
            "is_standalone": true
        },
        "questions": [
            {
                "number": 1,
                "question_type": "GAP",   // MCQ|MULTI|GAP|TABLE|SHORT
                "content": "The caller says ___ first.",
                "correct_answer": "hello",
                "explanation": "...",
                "group_instruction": "",
                "max_selections": 1,
                "choices": []
            }
        ]
    }
    """
    try:
        data = request.data
        file = request.FILES.get('file')
        if file:
            data = json.load(file)

        import_type = data.get('type', 'grammar').lower()

        from cefr.models import (
            CEFRTest, CEFRQuestion, CEFRChoice,
            CEFRReadingPassage, CEFRReadingQuestion, CEFRReadingChoice,
            CEFRListeningSection, CEFRListeningQuestion, CEFRListeningChoice,
        )

        if import_type == 'reading':
            # Multi-part mock (has "parts" key) — create one passage per part
            if 'parts' in data:
                parts_data = data['parts']
                if not parts_data:
                    return Response({'error': 'No parts provided.'}, status=400)
                created_passages = []
                with transaction.atomic():
                    for part in parts_data:
                        passage = CEFRReadingPassage.objects.create(
                            title=part.get('title', data.get('title', '')),
                            content=part.get('content', ''),
                            passage_number=part.get('passage_number', 1),
                            is_standalone=part.get('is_standalone', False),
                            level=data.get('level', ''),
                            time_limit=data.get('time_limit', 20),
                            difficulty=data.get('difficulty', 'MEDIUM').upper(),
                            is_premium=data.get('is_premium', False),
                            is_mock=data.get('is_mock', True),
                        )
                        for q_data in part.get('questions', []):
                            q = CEFRReadingQuestion.objects.create(
                                passage=passage,
                                number=q_data['number'],
                                question_type=_normalize_qt(q_data.get('question_type', 'MCQ')),
                                content=q_data.get('content') or '',
                                correct_answer=q_data.get('correct_answer', ''),
                                explanation=q_data.get('explanation') or '',
                                group_instruction=q_data.get('group_instruction', ''),
                                max_selections=q_data.get('max_selections', 1),
                                word_bank=q_data.get('word_bank', []) or [],
                                answer_review=q_data.get('answer_review', ''),
                            )
                            for c in q_data.get('choices', []):
                                CEFRReadingChoice.objects.create(question=q, option=c['option'], text=c['text'])
                        created_passages.append({'id': passage.id, 'title': passage.title, 'questions': passage.questions.count()})
                return Response({'part_count': len(parts_data), 'passages': created_passages})

            # Single passage — support both flat format (title/content at top level, same as IELTS)
            # and nested format (passage: {title, content, ...})
            passage_data = data.get('passage', {})
            title   = passage_data.get('title')   or data.get('title', '')
            content = passage_data.get('content') or data.get('content', '')
            pnum    = passage_data.get('passage_number') or data.get('passage_number', 1)
            standalone = passage_data.get('is_standalone')
            if standalone is None:
                standalone = data.get('is_standalone', True)

            with transaction.atomic():
                passage = CEFRReadingPassage.objects.create(
                    title=title,
                    content=content,
                    passage_number=pnum,
                    is_standalone=standalone,
                    level=data.get('level', ''),
                    time_limit=data.get('time_limit', 20),
                    difficulty=data.get('difficulty', 'MEDIUM').upper(),
                    is_premium=data.get('is_premium', False),
                    is_mock=data.get('is_mock', False),
                )
                for q_data in data.get('questions', []):
                    q = CEFRReadingQuestion.objects.create(
                        passage=passage,
                        number=q_data['number'],
                        question_type=_normalize_qt(q_data.get('question_type', 'MCQ')),
                        content=q_data.get('content') or '',
                        correct_answer=q_data.get('correct_answer', ''),
                        explanation=q_data.get('explanation') or '',
                        group_instruction=q_data.get('group_instruction', ''),
                        max_selections=q_data.get('max_selections', 1),
                        word_bank=q_data.get('word_bank', []) or [],
                        answer_review=q_data.get('answer_review', ''),
                    )
                    for c in q_data.get('choices', []):
                        CEFRReadingChoice.objects.create(question=q, option=c['option'], text=c['text'])
            return Response({'id': passage.id, 'title': passage.title, 'questions': passage.questions.count()})

        elif import_type == 'listening':
            # Multi-section mock (has "sections" key) — create one section per entry
            if 'sections' in data:
                sections_data = data['sections']
                if not sections_data:
                    return Response({'error': 'No sections provided.'}, status=400)
                created_sections = []
                with transaction.atomic():
                    for sec in sections_data:
                        section = CEFRListeningSection(
                            title=sec.get('title', data.get('title', '')),
                            section_number=sec.get('section_number', 1),
                            audio_url=sec.get('audio_url', ''),
                            transcript=sec.get('transcript', ''),
                            is_standalone=sec.get('is_standalone', False),
                            is_premium=data.get('is_premium', False),
                            level=data.get('level', ''),
                            time_limit=data.get('time_limit', 25),
                            is_mock=data.get('is_mock', True),
                        )
                        section.save()
                        for q_data in sec.get('questions', []):
                            q = CEFRListeningQuestion.objects.create(
                                section=section,
                                number=q_data['number'],
                                question_type=_normalize_qt(q_data.get('question_type', 'GAP')),
                                content=q_data.get('content') or '',
                                correct_answer=q_data.get('correct_answer', ''),
                                explanation=q_data.get('explanation') or '',
                                group_instruction=q_data.get('group_instruction', ''),
                                max_selections=q_data.get('max_selections', 1),
                                word_bank=q_data.get('word_bank', []) or [],
                                answer_review=q_data.get('answer_review', ''),
                            )
                            for c in q_data.get('choices', []):
                                CEFRListeningChoice.objects.create(question=q, option=c['option'], text=c['text'])
                        created_sections.append({'id': section.id, 'title': section.title, 'questions': section.questions.count()})
                return Response({'section_count': len(sections_data), 'sections': created_sections})

            # Single section — support flat format (fields at top level) and nested (section: {...})
            section_data = data.get('section', {})
            audio_file = request.FILES.get('audio')
            title        = section_data.get('title')        or data.get('title', '')
            section_num  = section_data.get('section_number') or data.get('section_number', 1)
            audio_url    = section_data.get('audio_url')    or data.get('audio_url', '')
            transcript   = section_data.get('transcript')   or data.get('transcript', '')
            standalone   = section_data.get('is_standalone')
            if standalone is None:
                standalone = data.get('is_standalone', True)

            with transaction.atomic():
                section = CEFRListeningSection(
                    title=title,
                    section_number=section_num,
                    audio_url=audio_url,
                    transcript=transcript,
                    is_standalone=standalone,
                    is_premium=data.get('is_premium', False),
                    level=data.get('level', ''),
                    time_limit=data.get('time_limit', 25),
                    is_mock=data.get('is_mock', False),
                )
                if audio_file:
                    section.audio_file = audio_file
                section.save()
                for q_data in data.get('questions', []):
                    q = CEFRListeningQuestion.objects.create(
                        section=section,
                        number=q_data['number'],
                        question_type=_normalize_qt(q_data.get('question_type', 'GAP')),
                        content=q_data.get('content') or '',
                        correct_answer=q_data.get('correct_answer', ''),
                        explanation=q_data.get('explanation') or '',
                        group_instruction=q_data.get('group_instruction', ''),
                        max_selections=q_data.get('max_selections', 1),
                        word_bank=q_data.get('word_bank', []) or [],
                        answer_review=q_data.get('answer_review', ''),
                    )
                    for c in q_data.get('choices', []):
                        CEFRListeningChoice.objects.create(question=q, option=c['option'], text=c['text'])
            return Response({'id': section.id, 'title': section.title, 'questions': section.questions.count()})

        else:
            # Grammar/Vocabulary (default)
            with transaction.atomic():
                test = CEFRTest.objects.create(
                    title=data['title'],
                    level=data['level'],
                    test_type=data.get('test_type', 'GRAMMAR'),
                    description=data.get('description', ''),
                    time_limit=data.get('time_limit', 60),
                    is_premium=data.get('is_premium', False),
                )
                for q_data in data.get('questions', []):
                    q = CEFRQuestion.objects.create(
                        test=test,
                        number=q_data['number'],
                        question_type=_normalize_qt(q_data.get('question_type', 'MCQ')),
                        content=q_data.get('content') or '',
                        passage=q_data.get('passage') or '',
                        correct_answer=q_data.get('correct_answer', ''),
                        explanation=q_data.get('explanation') or '',
                        group_instruction=q_data.get('group_instruction', ''),
                    )
                    for c in q_data.get('choices', []):
                        CEFRChoice.objects.create(question=q, option=c['option'], text=c['text'])

            return Response({
                'id': test.id,
                'title': test.title,
                'level': test.level,
                'questions': test.questions.count(),
            })

    except Exception as e:
        return Response({'error': str(e)}, status=400)


# ── ADMIN USER VIEWS ─────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_user_list(request):
    from accounts.models import User
    search = request.query_params.get('q', '')
    users = User.objects.all().order_by('-created_at')
    if search:
        users = users.filter(email__icontains=search) | users.filter(first_name__icontains=search)

    from tests_app.models import TestAttempt
    from accounts.models import UserStats
    result = []
    for u in users[:100]:
        try:
            exam_date = u.stats.sat_exam_date.isoformat() if u.stats.sat_exam_date else None
        except UserStats.DoesNotExist:
            exam_date = None
        result.append({
            'id': u.id,
            'email': u.email,
            'full_name': u.full_name,
            'first_name': u.first_name,
            'is_premium': u.is_premium,
            'is_staff': u.is_staff,
            'tests_taken': TestAttempt.objects.filter(user=u, status='COMPLETED').count(),
            'joined': u.created_at.isoformat(),
            'premium_until': u.premium_until.isoformat() if u.premium_until else None,
            'sat_exam_date': exam_date,
        })
    return Response(result)


@api_view(['POST'])
@permission_classes([IsAdminUser])
def admin_set_user_exam_date(request, user_id):
    """Admin: set SAT exam date for a specific user."""
    from accounts.models import User, UserStats
    from datetime import date
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=404)

    date_str = request.data.get('exam_date')
    if not date_str:
        return Response({'detail': 'exam_date required.'}, status=400)
    try:
        parts = date_str.split('-')
        d = date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return Response({'detail': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)

    stats, _ = UserStats.objects.get_or_create(user=user)
    stats.sat_exam_date = d
    stats.save(update_fields=['sat_exam_date'])
    return Response({'exam_date': d.isoformat()})


@api_view(['POST'])
@permission_classes([IsAdminUser])
def admin_toggle_premium(request, user_id):
    """
    Grant or revoke premium.
    Body: { "action": "grant", "days": 30 | 90 }
           { "action": "revoke" }
    If action is omitted, it toggles (legacy behaviour, always 30 days).
    """
    from accounts.models import User
    from django.utils import timezone
    from datetime import timedelta

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)

    action = request.data.get('action')
    days   = int(request.data.get('days', 30))
    if days not in (30, 90):
        days = 30

    if action == 'grant' or (action is None and not user.is_premium):
        # Extend from existing expiry if still premium, otherwise from now
        base = user.premium_until if (user.is_premium and user.premium_until and user.premium_until > timezone.now()) else timezone.now()
        user.is_premium     = True
        user.premium_until  = base + timedelta(days=days)
    else:
        # revoke (action=='revoke' or toggle-off)
        user.is_premium    = False
        user.premium_until = None

    user.save(update_fields=['is_premium', 'premium_until'])
    return Response({
        'is_premium':    user.is_premium,
        'premium_until': user.premium_until.isoformat() if user.premium_until else None,
    })


@api_view(['POST'])
@permission_classes([IsAdminUser])
def admin_toggle_staff(request, user_id):
    """Toggle is_staff for a user — grants/revokes admin panel access."""
    from accounts.models import User
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)
    # Prevent removing superuser staff status from the panel
    if user.is_superuser and not request.user.is_superuser:
        return Response({'error': 'Cannot modify superuser status.'}, status=403)
    user.is_staff = not user.is_staff
    user.save(update_fields=['is_staff'])
    return Response({'is_staff': user.is_staff})


@api_view(['GET', 'DELETE'])
@permission_classes([IsAdminUser])
def admin_user_detail(request, user_id):
    from accounts.models import User
    from tests_app.models import TestAttempt
    from ielts.models import IELTSAttempt, ReadingAnswer, ListeningAnswer
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)

    if request.method == 'DELETE':
        user.delete()
        return Response({'deleted': True})

    # SAT attempts
    sat_attempts = TestAttempt.objects.filter(user=user).select_related('result').order_by('-started_at')
    sat_completed = sat_attempts.filter(status='COMPLETED')
    sat_list = []
    for a in sat_attempts[:10]:
        score = None
        try:
            score = a.result.total_score
        except Exception:
            pass
        sat_list.append({
            'id': a.id,
            'test_name': str(a.test) if hasattr(a, 'test') and a.test else '—',
            'status': a.status,
            'score': score,
            'created_at': a.started_at.isoformat() if a.started_at else None,
            'type': 'SAT',
        })

    # IELTS attempts
    ielts_attempts = IELTSAttempt.objects.filter(user=user).order_by('-started_at')
    ielts_completed = ielts_attempts.filter(status='COMPLETED')
    ielts_list = []
    for a in ielts_attempts[:10]:
        ielts_list.append({
            'id': a.id,
            'test_name': str(a.test) if a.test else 'Practice',
            'status': a.status,
            'reading_band': float(a.reading_band) if a.reading_band else None,
            'listening_band': float(a.listening_band) if a.listening_band else None,
            'overall_band': float(a.overall_band) if a.overall_band else None,
            'created_at': a.started_at.isoformat(),
            'type': 'IELTS',
        })

    # Reading answer analytics
    reading_answers = ReadingAnswer.objects.filter(attempt__user=user)
    r_total = reading_answers.count()
    r_correct = reading_answers.filter(is_correct=True).count()

    # Listening answer analytics
    listening_answers = ListeningAnswer.objects.filter(attempt__user=user)
    l_total = listening_answers.count()
    l_correct = listening_answers.filter(is_correct=True).count()

    total_q = r_total + l_total
    total_correct = r_correct + l_correct

    return Response({
        'id': user.id,
        'email': user.email,
        'full_name': user.full_name,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'is_premium': user.is_premium,
        'is_staff': user.is_staff,
        'premium_until': user.premium_until.isoformat() if user.premium_until else None,
        'joined': user.created_at.isoformat(),
        # Counts
        'sat_taken': sat_completed.count(),
        'ielts_taken': ielts_completed.count(),
        'tests_taken': sat_completed.count() + ielts_completed.count(),
        # Question analytics
        'analytics': {
            'total_questions': total_q,
            'total_correct': total_correct,
            'accuracy_pct': round(total_correct / total_q * 100) if total_q else 0,
            'reading': {'total': r_total, 'correct': r_correct, 'pct': round(r_correct / r_total * 100) if r_total else 0},
            'listening': {'total': l_total, 'correct': l_correct, 'pct': round(l_correct / l_total * 100) if l_total else 0},
        },
        'sat_attempts': sat_list,
        'ielts_attempts': ielts_list,
    })


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_sat_tests(request):
    from tests_app.models import Test
    tests = Test.objects.all().order_by('-year', '-month')
    result = []
    for t in tests:
        result.append({
            'id': t.id,
            'display_name': t.display_name,
            'year': t.year,
            'month': t.get_month_display(),
            'month_num': t.month,
            'form': t.form,
            'test_type': t.test_type,
            'test_mode': t.test_mode,
            'is_international': t.is_international,
            'is_premium': t.is_premium,
            'is_active': t.is_active,
            'sections': t.sections.count(),
        })
    return Response(result)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAdminUser])
def admin_sat_test_update(request, test_id):
    from tests_app.models import Test
    from django.shortcuts import get_object_or_404
    test = get_object_or_404(Test, id=test_id)

    if request.method == 'DELETE':
        test.delete()
        return Response({'deleted': True})

    if 'is_active' in request.data:
        test.is_active = request.data['is_active']
    if 'is_premium' in request.data:
        test.is_premium = request.data['is_premium']
    if 'test_mode' in request.data and request.data['test_mode'] in ('FULL', 'INDIVIDUAL'):
        test.test_mode = request.data['test_mode']
    test.save()
    return Response({'id': test.id, 'is_active': test.is_active, 'is_premium': test.is_premium, 'test_mode': test.test_mode})


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_sat_questions(request):
    from tests_app.models import Question, Module, TestSection
    test_id = request.query_params.get('test_id')
    section = request.query_params.get('section', '').upper()
    module_num = request.query_params.get('module')
    difficulty_variant = request.query_params.get('difficulty_variant', '').upper()

    if not test_id:
        return Response([])

    qs = Question.objects.select_related('module__section__test').prefetch_related('choices')
    qs = qs.filter(module__section__test_id=test_id)
    if section:
        qs = qs.filter(module__section__section_type=section)
    if module_num:
        qs = qs.filter(module__module_number=module_num)
    if difficulty_variant:
        qs = qs.filter(module__difficulty_variant=difficulty_variant)

    result = []
    for q in qs.order_by('module__section__section_type', 'module__module_number', 'number'):
        result.append({
            'id': q.id,
            'number': q.number,
            'question_type': q.question_type,
            'difficulty': q.difficulty,
            'content': q.content,
            'passage': q.passage,
            'table_data': q.table_data,
            'correct_answer': q.correct_answer,
            'explanation': q.explanation,
            'image': q.image.url if q.image else None,
            'section': q.module.section.section_type,
            'module': q.module.module_number,
            'difficulty_variant': q.module.difficulty_variant,
            'choices': [{'option': c.option, 'text': c.text, 'image': c.image.url if c.image else None} for c in q.choices.all()],
        })
    return Response(result)


@api_view(['PATCH'])
@permission_classes([IsAdminUser])
def admin_ielts_reading_update(request, pk):
    from ielts.models import ReadingPassage
    from django.shortcuts import get_object_or_404
    p = get_object_or_404(ReadingPassage, id=pk)
    for field in ['title', 'difficulty', 'is_premium', 'is_standalone', 'time_limit']:
        if field in request.data:
            setattr(p, field, request.data[field])
    p.save()
    return Response({'id': p.id, 'title': p.title, 'difficulty': p.difficulty, 'is_premium': p.is_premium})


@api_view(['PATCH'])
@permission_classes([IsAdminUser])
def admin_ielts_listening_update(request, pk):
    from ielts.models import ListeningSection
    from django.shortcuts import get_object_or_404
    s = get_object_or_404(ListeningSection, id=pk)
    for field in ['title', 'difficulty', 'is_premium', 'is_standalone', 'transcript']:
        if field in request.data:
            setattr(s, field, request.data[field])
    s.save()
    return Response({'id': s.id, 'title': s.title, 'difficulty': s.difficulty, 'is_premium': s.is_premium})


@api_view(['POST'])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser])
def admin_ielts_listening_upload_audio(request, pk):
    from ielts.models import ListeningSection
    from django.shortcuts import get_object_or_404
    s = get_object_or_404(ListeningSection, id=pk)
    audio = request.FILES.get('audio')
    if not audio:
        return Response({'error': 'No audio file provided.'}, status=400)
    s.audio_file = audio
    s.save(update_fields=['audio_file'])
    return Response({'id': s.id, 'audio_url': s.audio_file.url if s.audio_file else None})


@api_view(['POST', 'DELETE'])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser, JSONParser])
def admin_ielts_test_audio(request, pk):
    from ielts.models import IELTSTest
    from django.shortcuts import get_object_or_404
    t = get_object_or_404(IELTSTest, id=pk)
    if request.method == 'DELETE':
        if t.audio_file:
            t.audio_file.delete(save=False)
        t.audio_url = ''
        t.save(update_fields=['audio_file', 'audio_url'])
        return Response({'ok': True})
    audio = request.FILES.get('audio')
    if not audio:
        return Response({'error': 'No audio file provided.'}, status=400)
    t.audio_file = audio
    t.save(update_fields=['audio_file'])
    return Response({'id': t.id, 'audio_url': t.audio_file.url if t.audio_file else None})


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_ielts_reading_list(request):
    from ielts.models import ReadingPassage, IELTSTest
    items = ReadingPassage.objects.order_by('passage_number', '-created_at')
    test_map = {}
    for t in IELTSTest.objects.prefetch_related('passages').all():
        for p in t.passages.all():
            test_map[p.id] = {'test_id': t.id, 'test_title': t.title, 'test_is_premium': t.is_premium}
    return Response([{
        'id': p.id, 'title': p.title,
        'passage_number': p.passage_number,
        'time_limit': p.time_limit,
        'difficulty': p.difficulty,
        'is_standalone': p.is_standalone,
        'is_premium': p.is_premium,
        'question_count': p.questions.count(),
        'test_id': test_map.get(p.id, {}).get('test_id'),
        'test_title': test_map.get(p.id, {}).get('test_title'),
        'test_is_premium': test_map.get(p.id, {}).get('test_is_premium', False),
    } for p in items])


@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def admin_ielts_reading_delete(request, pk):
    from ielts.models import ReadingPassage
    from django.shortcuts import get_object_or_404
    get_object_or_404(ReadingPassage, id=pk).delete()
    return Response(status=204)


@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def admin_ielts_reading_delete_all(request):
    from ielts.models import ReadingPassage, IELTSTest
    count, _ = ReadingPassage.objects.all().delete()
    # Also clean up orphaned IELTSTest records (no passages left)
    IELTSTest.objects.filter(passages__isnull=True).delete()
    return Response({'deleted': count})


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_ielts_listening_list(request):
    from ielts.models import ListeningSection, IELTSTest
    items = ListeningSection.objects.select_related().order_by('section_number', '-created_at')
    test_map = {}
    for t in IELTSTest.objects.prefetch_related('listening_sections').all():
        for s in t.listening_sections.all():
            test_audio = t.audio_url or (t.audio_file.url if t.audio_file else None)
            test_map[s.id] = {'test_id': t.id, 'test_title': t.title, 'test_audio_url': test_audio, 'test_is_premium': t.is_premium}
    return Response([{
        'id': s.id, 'title': s.title,
        'section_number': s.section_number,
        'difficulty': s.difficulty,
        'is_standalone': s.is_standalone,
        'is_premium': s.is_premium,
        'question_count': s.questions.count(),
        'audio_file': s.audio_file.url if s.audio_file else None,
        'test_id': test_map.get(s.id, {}).get('test_id'),
        'test_title': test_map.get(s.id, {}).get('test_title'),
        'test_audio_url': test_map.get(s.id, {}).get('test_audio_url'),
        'test_is_premium': test_map.get(s.id, {}).get('test_is_premium', False),
    } for s in items])


@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def admin_ielts_listening_delete(request, pk):
    from ielts.models import ListeningSection
    from django.shortcuts import get_object_or_404
    get_object_or_404(ListeningSection, id=pk).delete()
    return Response(status=204)


@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def admin_ielts_listening_delete_all(request):
    from ielts.models import ListeningSection
    count, _ = ListeningSection.objects.all().delete()
    return Response({'deleted': count})


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_ielts_speaking_list(request):
    from ielts.models import SpeakingTask
    tasks = SpeakingTask.objects.all().order_by('-created_at')
    result = []
    for t in tasks:
        result.append({
            'id': t.id,
            'title': t.title,
            'test_type': t.test_type,
            'part': t.part,
            'part_display': f'Part {t.part}' if t.part > 0 else 'Full Mock',
            'topic': t.topic,
            'question_count': len(t.questions) if t.questions else (len(t.parts_data) if t.parts_data else 0),
            'is_premium': t.is_premium,
            'created_at': str(t.created_at)[:10],
        })
    return Response(result)


@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def admin_ielts_speaking_delete(request, pk):
    from ielts.models import SpeakingTask
    from django.shortcuts import get_object_or_404
    get_object_or_404(SpeakingTask, id=pk).delete()
    return Response(status=204)


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_ielts_writing_list(request):
    from ielts.models import WritingTask
    items = WritingTask.objects.order_by('-created_at')
    return Response([{
        'id': t.id,
        'title': t.title,
        'task_type': t.task_type,
        'test_type': t.test_type,
        'difficulty': t.difficulty,
        'min_words': t.min_words,
        'time_limit': t.time_limit,
        'is_premium': t.is_premium,
        'has_image': bool(t.image),
        'recommendations': t.recommendations or [],
    } for t in items])


@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def admin_ielts_writing_delete(request, pk):
    from ielts.models import WritingTask
    from django.shortcuts import get_object_or_404
    get_object_or_404(WritingTask, id=pk).delete()
    return Response(status=204)


@api_view(['POST'])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser])
def admin_ielts_writing_upload_image(request, pk):
    """Upload Task 1 chart/diagram image."""
    from ielts.models import WritingTask
    from django.shortcuts import get_object_or_404
    task = get_object_or_404(WritingTask, id=pk)
    image = request.FILES.get('image')
    if not image:
        return Response({'error': 'No image file provided.'}, status=400)
    task.image = image
    task.save()
    return Response({'id': task.id, 'image': task.image.url})


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_ielts_content(request):
    from ielts.models import ReadingPassage, ListeningSection, SpeakingTask, WritingTask
    return Response({
        'reading': ReadingPassage.objects.count(),
        'listening': ListeningSection.objects.count(),
        'speaking': SpeakingTask.objects.count(),
        'writing': WritingTask.objects.count(),
    })


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_cefr_tests(request):
    from cefr.models import CEFRTest
    level = request.query_params.get('level')
    tests = CEFRTest.objects.all().order_by('level', '-created_at')
    if level:
        tests = tests.filter(level=level)
    return Response([{
        'id': t.id,
        'title': t.title,
        'level': t.level,
        'test_type': t.test_type,
        'time_limit': t.time_limit,
        'is_premium': t.is_premium,
        'question_count': t.questions.count(),
    } for t in tests])


@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def admin_cefr_test_delete(request, pk):
    from cefr.models import CEFRTest
    from django.shortcuts import get_object_or_404
    get_object_or_404(CEFRTest, id=pk).delete()
    return Response(status=204)


@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def admin_cefr_reading_delete(request, pk):
    from cefr.models import CEFRReadingPassage
    from django.shortcuts import get_object_or_404
    get_object_or_404(CEFRReadingPassage, id=pk).delete()
    return Response(status=204)


@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def admin_cefr_reading_delete_all(request):
    from cefr.models import CEFRReadingPassage
    count, _ = CEFRReadingPassage.objects.all().delete()
    return Response({'deleted': count})


@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def admin_cefr_listening_delete(request, pk):
    from cefr.models import CEFRListeningSection
    from django.shortcuts import get_object_or_404
    get_object_or_404(CEFRListeningSection, id=pk).delete()
    return Response(status=204)


@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def admin_cefr_listening_delete_all(request):
    from cefr.models import CEFRListeningSection
    count, _ = CEFRListeningSection.objects.all().delete()
    return Response({'deleted': count})


# ── IELTS admin detail endpoints (for preview modal) ─────────────────────────

@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_ielts_reading_detail(request, pk):
    from ielts.models import ReadingPassage
    from django.shortcuts import get_object_or_404
    p = get_object_or_404(ReadingPassage, id=pk)
    questions = p.questions.prefetch_related('choices').order_by('number')
    return Response({
        'id': p.id, 'title': p.title, 'content': p.content,
        'passage_number': p.passage_number, 'difficulty': p.difficulty,
        'time_limit': p.time_limit, 'is_standalone': p.is_standalone, 'is_premium': p.is_premium,
        'image': p.image.url if p.image else None,
        'questions': [{
            'id': q.id, 'number': q.number, 'question_type': q.question_type,
            'content': q.content, 'correct_answer': q.correct_answer,
            'explanation': q.explanation, 'group_instruction': q.group_instruction,
            'max_selections': q.max_selections,
            'image': q.image.url if q.image else None,
            'choices': [{'option': c.option, 'text': c.text} for c in q.choices.all()],
        } for q in questions]
    })


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_ielts_listening_detail(request, pk):
    from ielts.models import ListeningSection
    from django.shortcuts import get_object_or_404
    s = get_object_or_404(ListeningSection, id=pk)
    questions = s.questions.prefetch_related('choices').order_by('number')
    return Response({
        'id': s.id, 'title': s.title, 'transcript': s.transcript,
        'section_number': s.section_number, 'difficulty': s.difficulty,
        'is_standalone': s.is_standalone, 'is_premium': s.is_premium,
        'audio_file': s.audio_file.url if s.audio_file else None,
        'questions': [{
            'id': q.id, 'number': q.number, 'question_type': q.question_type,
            'content': q.content, 'correct_answer': q.correct_answer,
            'explanation': q.explanation, 'group_instruction': q.group_instruction,
            'max_selections': q.max_selections,
            'choices': [{'option': c.option, 'text': c.text} for c in q.choices.all()],
        } for q in questions]
    })


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_ielts_tests_list(request):
    from ielts.models import IELTSTest
    tests = IELTSTest.objects.all().order_by('-created_at')
    return Response([{
        'id': t.id, 'title': t.title, 'test_type': t.test_type,
        'is_premium': t.is_premium, 'is_active': t.is_active,
        'created_at': str(t.created_at)[:10],
        'passage_count': t.passages.count(),
        'section_count': t.listening_sections.count(),
    } for t in tests])


@api_view(['PATCH'])
@permission_classes([IsAdminUser])
def admin_ielts_test_premium(request, pk):
    """Toggle or set is_premium on an IELTSTest (mock test)."""
    from ielts.models import IELTSTest
    from django.shortcuts import get_object_or_404
    test = get_object_or_404(IELTSTest, id=pk)
    is_premium = request.data.get('is_premium')
    if is_premium is None:
        is_premium = not test.is_premium  # toggle
    test.is_premium = bool(is_premium)
    test.save(update_fields=['is_premium'])
    return Response({'id': test.id, 'is_premium': test.is_premium})


@api_view(['GET', 'DELETE'])
@permission_classes([IsAdminUser])
def admin_ielts_test_detail(request, pk):
    from ielts.models import IELTSTest
    from django.shortcuts import get_object_or_404
    test = get_object_or_404(IELTSTest, id=pk)
    if request.method == 'DELETE':
        test.delete()
        return Response(status=204)
    passages = test.passages.all().order_by('passage_number')
    sections = test.listening_sections.all().order_by('section_number')
    return Response({
        'id': test.id, 'title': test.title, 'test_type': test.test_type,
        'is_premium': test.is_premium, 'is_active': test.is_active,
        'passages': [{
            'id': p.id, 'title': p.title, 'passage_number': p.passage_number,
            'question_count': p.questions.count(),
        } for p in passages],
        'sections': [{
            'id': s.id, 'title': s.title, 'section_number': s.section_number,
            'question_count': s.questions.count(),
            'audio_file': s.audio_file.url if s.audio_file else None,
        } for s in sections],
    })


# ── CEFR admin list + detail endpoints ───────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_cefr_reading_list(request):
    from cefr.models import CEFRReadingPassage
    items = CEFRReadingPassage.objects.order_by('-created_at')
    return Response([{
        'id': p.id, 'title': p.title, 'level': p.level,
        'passage_number': p.passage_number, 'difficulty': p.difficulty,
        'time_limit': p.time_limit, 'is_standalone': p.is_standalone,
        'is_mock': p.is_mock, 'is_premium': p.is_premium,
        'question_count': p.questions.count(),
    } for p in items])


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_cefr_reading_detail(request, pk):
    from cefr.models import CEFRReadingPassage
    from django.shortcuts import get_object_or_404
    p = get_object_or_404(CEFRReadingPassage, id=pk)
    questions = p.questions.prefetch_related('choices').order_by('number')
    return Response({
        'id': p.id, 'title': p.title, 'content': p.content,
        'level': p.level, 'passage_number': p.passage_number, 'difficulty': p.difficulty,
        'questions': [{
            'id': q.id, 'number': q.number, 'question_type': q.question_type,
            'content': q.content, 'correct_answer': q.correct_answer,
            'explanation': q.explanation, 'group_instruction': q.group_instruction,
            'max_selections': q.max_selections,
            'choices': [{'option': c.option, 'text': c.text} for c in q.choices.all()],
        } for q in questions]
    })


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_cefr_listening_list(request):
    from cefr.models import CEFRListeningSection
    items = CEFRListeningSection.objects.order_by('-created_at')
    return Response([{
        'id': s.id, 'title': s.title, 'level': s.level,
        'section_number': s.section_number, 'time_limit': s.time_limit,
        'is_standalone': s.is_standalone, 'is_mock': s.is_mock, 'is_premium': s.is_premium,
        'audio_file': s.audio_file.url if s.audio_file else None,
        'has_audio': bool(s.audio_file or s.audio_url),
        'question_count': s.questions.count(),
    } for s in items])


@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_cefr_listening_detail(request, pk):
    from cefr.models import CEFRListeningSection
    from django.shortcuts import get_object_or_404
    s = get_object_or_404(CEFRListeningSection, id=pk)
    questions = s.questions.prefetch_related('choices').order_by('number')
    return Response({
        'id': s.id, 'title': s.title, 'transcript': s.transcript,
        'level': s.level, 'section_number': s.section_number,
        'audio_file': s.audio_file.url if s.audio_file else None,
        'audio_url': s.audio_url,
        'questions': [{
            'id': q.id, 'number': q.number, 'question_type': q.question_type,
            'content': q.content, 'correct_answer': q.correct_answer,
            'explanation': q.explanation, 'group_instruction': q.group_instruction,
            'max_selections': q.max_selections,
            'choices': [{'option': c.option, 'text': c.text} for c in q.choices.all()],
        } for q in questions]
    })


@api_view(['POST'])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser])
def admin_cefr_listening_audio(request, pk):
    from cefr.models import CEFRListeningSection
    from django.shortcuts import get_object_or_404
    s = get_object_or_404(CEFRListeningSection, id=pk)
    audio = request.FILES.get('audio')
    if not audio:
        return Response({'error': 'No audio file provided.'}, status=400)
    s.audio_file = audio
    s.save(update_fields=['audio_file'])
    return Response({'id': s.id, 'audio_url': s.audio_file.url if s.audio_file else None})


# ══════════════════════════════════════════════════════════════════
#  ADMIN — SAT BANK QUESTIONS
# ══════════════════════════════════════════════════════════════════

@api_view(['GET'])
@permission_classes([IsAdminUser])
def admin_bank_questions(request):
    """List bank questions with optional filters."""
    from tests_app.models import BankQuestion
    from django.shortcuts import get_object_or_404

    subject = request.GET.get('subject', '')
    category = request.GET.get('category', '')
    difficulty = request.GET.get('difficulty', '')
    q_type = request.GET.get('question_type', '')
    search = request.GET.get('search', '')
    topic = request.GET.get('topic', '')
    topics_only = request.GET.get('topics_only', '')
    page = int(request.GET.get('page', 1))
    page_size = min(int(request.GET.get('page_size', 50)), 200)

    qs = BankQuestion.objects.all()
    if subject:
        qs = qs.filter(subject=subject)
    if category:
        qs = qs.filter(category=category)
    if difficulty:
        qs = qs.filter(difficulty=difficulty)
    if q_type:
        qs = qs.filter(question_type=q_type)
    if topic:
        qs = qs.filter(topic__icontains=topic)
    if search:
        qs = qs.filter(content__icontains=search) | qs.filter(topic__icontains=search)

    # Return only unique topics (for dropdown)
    if topics_only:
        topics_qs = qs.values_list('topic', flat=True).distinct().order_by('topic')
        return Response({'topics': list(topics_qs)})

    total = qs.count()
    start = (page - 1) * page_size
    items = qs[start:start + page_size]

    def serialize(q):
        req = request
        def img_url(field):
            if field:
                try:
                    return req.build_absolute_uri(field.url)
                except Exception:
                    return field.url
            return None

        return {
            'id': q.id,
            'subject': q.subject,
            'category': q.category,
            'question_type': q.question_type,
            'topic': q.topic,
            'content': q.content,
            'passage': q.passage,
            'table_data': q.table_data,
            'image': img_url(q.image),
            'difficulty': q.difficulty,
            'choice_a': q.choice_a,
            'choice_b': q.choice_b,
            'choice_c': q.choice_c,
            'choice_d': q.choice_d,
            'choice_a_image': img_url(q.choice_a_image),
            'choice_b_image': img_url(q.choice_b_image),
            'choice_c_image': img_url(q.choice_c_image),
            'choice_d_image': img_url(q.choice_d_image),
            'correct_answer': q.correct_answer,
            'explanation': q.explanation,
            'year': q.year,
            'source': q.source,
            'created_at': q.created_at,
        }

    return Response({'total': total, 'page': page, 'page_size': page_size, 'results': [serialize(q) for q in items]})


@api_view(['POST'])
@permission_classes([IsAdminUser])
def admin_bank_question_create(request):
    from tests_app.models import BankQuestion
    data = request.data
    files = request.FILES
    q = BankQuestion.objects.create(
        subject=data.get('subject', 'Matematika'),
        category=data.get('category', ''),
        question_type=data.get('question_type', 'MCQ'),
        topic=data.get('topic', ''),
        content=data.get('content', ''),
        passage=data.get('passage', ''),
        table_data=data.get('table_data') or None,
        difficulty=data.get('difficulty', 'medium'),
        choice_a=data.get('choice_a', ''),
        choice_b=data.get('choice_b', ''),
        choice_c=data.get('choice_c', ''),
        choice_d=data.get('choice_d', ''),
        correct_answer=data.get('correct_answer', ''),
        explanation=data.get('explanation', ''),
        year=data.get('year') or None,
        source=data.get('source', ''),
    )
    for letter in ['a', 'b', 'c', 'd']:
        f = files.get(f'choice_{letter}_image')
        if f:
            setattr(q, f'choice_{letter}_image', f)
    if files.get('image'):
        q.image = files['image']
    q.save()
    return Response({'id': q.id, 'created': True}, status=201)


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAdminUser])
def admin_bank_question_detail(request, pk):
    from tests_app.models import BankQuestion
    from django.shortcuts import get_object_or_404
    q = get_object_or_404(BankQuestion, id=pk)

    if request.method == 'DELETE':
        q.delete()
        return Response(status=204)

    if request.method == 'PUT':
        data = request.data
        files = request.FILES
        for field in ['subject', 'category', 'question_type', 'topic', 'content', 'passage',
                      'difficulty', 'choice_a', 'choice_b', 'choice_c', 'choice_d',
                      'correct_answer', 'explanation', 'source']:
            if field in data:
                setattr(q, field, data[field])
        if 'table_data' in data:
            q.table_data = data['table_data'] or None
        if 'year' in data:
            q.year = data['year'] or None
        for letter in ['a', 'b', 'c', 'd']:
            f = files.get(f'choice_{letter}_image')
            if f:
                setattr(q, f'choice_{letter}_image', f)
        if files.get('image'):
            q.image = files['image']
        q.save()
        return Response({'updated': True})

    def img_url(field):
        if field:
            try:
                return request.build_absolute_uri(field.url)
            except Exception:
                return field.url
        return None

    # GET
    return Response({
        'id': q.id,
        'subject': q.subject,
        'category': q.category,
        'question_type': q.question_type,
        'topic': q.topic,
        'content': q.content,
        'passage': q.passage,
        'table_data': q.table_data,
        'image': img_url(q.image),
        'difficulty': q.difficulty,
        'choice_a': q.choice_a,
        'choice_b': q.choice_b,
        'choice_c': q.choice_c,
        'choice_d': q.choice_d,
        'choice_a_image': img_url(q.choice_a_image),
        'choice_b_image': img_url(q.choice_b_image),
        'choice_c_image': img_url(q.choice_c_image),
        'choice_d_image': img_url(q.choice_d_image),
        'correct_answer': q.correct_answer,
        'explanation': q.explanation,
        'year': q.year,
        'source': q.source,
        'created_at': q.created_at,
    })


@api_view(['POST', 'DELETE'])
@permission_classes([IsAdminUser])
def admin_bank_question_choice_image(request, pk, letter):
    """Upload or delete image for a specific choice (a/b/c/d) or the question itself (q)."""
    from tests_app.models import BankQuestion
    from django.shortcuts import get_object_or_404
    q = get_object_or_404(BankQuestion, id=pk)

    def img_url(field):
        if field:
            try:
                return request.build_absolute_uri(field.url)
            except Exception:
                return field.url
        return None

    if request.method == 'DELETE':
        if letter == 'q':
            if q.image:
                q.image.delete(save=False)
                q.image = None
        elif letter in ['a', 'b', 'c', 'd']:
            field = getattr(q, f'choice_{letter}_image', None)
            if field:
                field.delete(save=False)
                setattr(q, f'choice_{letter}_image', None)
        else:
            return Response({'error': 'Invalid letter.'}, status=400)
        q.save()
        return Response({'url': None})

    f = request.FILES.get('image')
    if not f:
        return Response({'error': 'No image file provided.'}, status=400)
    if letter == 'q':
        q.image = f
    elif letter in ['a', 'b', 'c', 'd']:
        setattr(q, f'choice_{letter}_image', f)
    else:
        return Response({'error': 'Invalid letter. Use a/b/c/d or q.'}, status=400)
    q.save()
    field = q.image if letter == 'q' else getattr(q, f'choice_{letter}_image')
    return Response({'url': img_url(field)})
