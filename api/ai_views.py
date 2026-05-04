"""
AI Tutor views — streaming chat for SAT / IELTS / CEFR
"""
import json
import base64
from openai import OpenAI
from django.conf import settings
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.parsers import MultiPartParser, JSONParser
from rest_framework.response import Response

from ai_chat.models import AIStructure, AIConversation, AIMessage

# ── System prompt builder ──────────────────────────────────────────────────────

SUBJECT_CONTEXT = {
    'SAT': {
        'role': 'an expert SAT tutor with 10+ years of experience',
        'sections': {
            'math': 'SAT Math (Algebra, Advanced Math, Problem-Solving & Data Analysis, Geometry & Trigonometry)',
            'reading_writing': 'SAT Reading & Writing (Information and Ideas, Craft and Structure, Expression of Ideas, Standard English Conventions)',
            'general': 'all SAT sections',
        },
        'advice': (
            'Help students understand SAT question patterns. '
            'When solving math, show step-by-step solutions with clear reasoning. '
            'For Reading & Writing, explain the rhetorical purpose and text structure. '
            'Identify which SAT content domain the question belongs to. '
            'Give vocabulary tips and grammar rules when relevant.'
        ),
    },
    'IELTS': {
        'role': 'an expert IELTS tutor and examiner with 10+ years of experience',
        'sections': {
            'reading': 'IELTS Academic Reading (skimming, scanning, True/False/Not Given, matching headings, etc.)',
            'listening': 'IELTS Listening (note completion, multiple choice, map labelling, etc.)',
            'writing': 'IELTS Academic Writing (Task 1 data description, Task 2 discursive essays)',
            'speaking': 'IELTS Speaking (Part 1/2/3, fluency, coherence, vocabulary, pronunciation, grammar)',
            'general': 'all IELTS sections',
        },
        'advice': (
            'Teach IELTS strategies and band descriptors. '
            'For Writing Task 2, provide band 9 sample essays and critique student essays with specific band score feedback. '
            'For Writing Task 1, explain how to describe graphs/charts/diagrams. '
            'For Speaking, teach answer structures (PEEL, STAR) and model responses. '
            'For Reading, teach how to find answers efficiently. '
            'Always explain why an answer is correct or incorrect. '
            'Give vocabulary suggestions and collocations for higher band scores.'
        ),
    },
    'CEFR': {
        'role': 'an expert CEFR English language tutor (A1–C2)',
        'sections': {
            'grammar': 'CEFR Grammar (from basic A1 structures to complex C2 grammar)',
            'reading': 'CEFR Reading comprehension',
            'listening': 'CEFR Listening comprehension',
            'general': 'all CEFR levels',
        },
        'advice': (
            'Explain grammar rules clearly with examples at the appropriate CEFR level. '
            'Identify the student\'s level from their performance and tailor explanations accordingly. '
            'Provide example sentences, exercises, and common mistakes to avoid. '
            'For reading/listening, explain comprehension strategies.'
        ),
    },
}


def _build_system_prompt(subject, section, user, structures):
    ctx = SUBJECT_CONTEXT.get(subject, SUBJECT_CONTEXT['SAT'])
    section_desc = ctx['sections'].get(section, ctx['sections'].get('general', ''))

    # Collect user performance summary
    perf_lines = _get_user_performance(user, subject)

    # Collect relevant structures
    struct_text = ''
    if structures:
        parts = []
        for s in structures:
            img_note = ' [has diagram/image]' if s.image else ''
            parts.append(f"### {s.title}{img_note}\n{s.content}")
        struct_text = '\n\n'.join(parts)

    prompt = f"""You are {ctx['role']} for the {subject} exam, specializing in {section_desc}.

## Your role
{ctx['advice']}

## Important rules
- Always respond in **English only**
- Be concise but thorough — give complete, accurate explanations
- When solving problems: show clear step-by-step reasoning
- When a student asks for examples: provide 2–3 varied examples
- When explaining a topic: identify the relevant {subject} domain/category
- For writing samples: provide full model answers with commentary
- Use markdown formatting (headers, bold, bullet points, code blocks for math)
- Always be encouraging and constructive
- If the student shares an image with a question, analyze it carefully

## Student performance
{perf_lines if perf_lines else 'No performance data available yet.'}
"""

    if struct_text:
        prompt += f"""
## Teaching structures & frameworks to use
Use these structures when explaining topics or answering questions in this area:

{struct_text}
"""

    prompt += """
## Response format
- Use clear markdown with headers (##, ###) for organization
- Use **bold** for key terms
- Use bullet points and numbered lists for steps
- For math: use LaTeX notation \\( ... \\) for inline and \\[ ... \\] for block equations
- Keep responses focused and actionable
"""
    return prompt


def _get_user_performance(user, subject):
    lines = []
    try:
        if subject == 'SAT':
            from tests_app.models import TestResult
            results = TestResult.objects.filter(user=user).order_by('-calculated_at')[:5]
            if results:
                best = results.order_by('-total_score').first()
                lines.append(f"- Best SAT score: {best.total_score}/1600 (Math: {best.math_score}, R&W: {best.english_score})")
                avg = sum(r.total_score for r in results) / len(results)
                lines.append(f"- Recent average: {avg:.0f}/1600 over {len(results)} attempts")
        elif subject == 'IELTS':
            from ielts.models import IELTSAttempt
            attempts = IELTSAttempt.objects.filter(user=user, status='COMPLETED').order_by('-completed_at')[:10]
            if attempts:
                lines.append(f"- IELTS attempts: {len(attempts)} completed")
        elif subject == 'CEFR':
            from cefr.models import CEFRAttempt
            attempts = CEFRAttempt.objects.filter(user=user, status='COMPLETED').order_by('-completed_at')[:10]
            if attempts:
                lines.append(f"- CEFR attempts: {len(attempts)} completed")
    except Exception:
        pass
    return '\n'.join(lines)


# ── Chat endpoint (streaming SSE) ─────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, JSONParser])
def ai_chat(request):
    """
    POST multipart or JSON:
      - subject: SAT | IELTS | CEFR
      - section: math | reading_writing | reading | listening | writing | speaking | grammar | general
      - message: str
      - image: file (optional)
      - conversation_id: int (optional, to continue existing)
    Returns: StreamingHttpResponse (SSE)
    """
    subject = request.data.get('subject', 'SAT').upper()
    section = request.data.get('section', 'general').lower()
    message = request.data.get('message', '').strip()
    conv_id = request.data.get('conversation_id')
    image_file = request.FILES.get('image')

    if not message and not image_file:
        return Response({'error': 'message or image required'}, status=400)

    # Get or create conversation
    if conv_id:
        conv = get_object_or_404(AIConversation, id=conv_id, user=request.user)
    else:
        title = message[:80] if message else 'Image question'
        conv = AIConversation.objects.create(
            user=request.user,
            subject=subject,
            section=section,
            title=title,
        )

    # Save user message
    user_msg = AIMessage(conversation=conv, role='user', content=message)
    if image_file:
        user_msg.image = image_file
    user_msg.save()

    # Load relevant structures
    structures = AIStructure.objects.filter(
        subject__in=[subject, 'GENERAL'],
        section__in=[section, 'general'],
        is_active=True,
    ).order_by('subject', 'section', 'title')

    # Build system prompt
    system_prompt = _build_system_prompt(subject, section, request.user, structures)

    # Build message history (last 20 messages)
    history = conv.messages.order_by('-created_at')[:20]
    history = list(reversed(history))

    # Build Anthropic messages
    anthropic_messages = []
    for msg in history:
        if msg.id == user_msg.id:
            continue  # will be added with image below
        anthropic_messages.append({
            'role': msg.role,
            'content': msg.content,
        })

    # Add current user message (possibly with image)
    if image_file:
        user_msg.image.seek(0)
        img_data = base64.standard_b64encode(user_msg.image.read()).decode('utf-8')
        name = image_file.name.lower()
        if name.endswith('.png'):
            media_type = 'image/png'
        elif name.endswith('.gif'):
            media_type = 'image/gif'
        elif name.endswith('.webp'):
            media_type = 'image/webp'
        else:
            media_type = 'image/jpeg'

        content_parts = []
        if message:
            content_parts.append({'type': 'text', 'text': message})
        content_parts.append({
            'type': 'image_url',
            'image_url': {'url': f'data:{media_type};base64,{img_data}'},
        })
        anthropic_messages.append({'role': 'user', 'content': content_parts})
    else:
        anthropic_messages.append({'role': 'user', 'content': message})

    def stream_response():
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        full_response = ''

        # Send conversation_id first
        yield f"data: {json.dumps({'type': 'conversation_id', 'id': conv.id, 'title': conv.title})}\n\n"

        try:
            openai_messages = [{'role': 'system', 'content': system_prompt}] + anthropic_messages

            stream = client.chat.completions.create(
                model='gpt-4o',
                max_tokens=2048,
                messages=openai_messages,
                stream=True,
            )
            for chunk in stream:
                text = chunk.choices[0].delta.content
                if text:
                    full_response += text
                    yield f"data: {json.dumps({'type': 'delta', 'text': text})}\n\n"

            # Save assistant message
            AIMessage.objects.create(
                conversation=conv,
                role='assistant',
                content=full_response,
            )
            conv.save(update_fields=['updated_at'])

            yield f"data: {json.dumps({'type': 'done', 'conversation_id': conv.id})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    response = StreamingHttpResponse(stream_response(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


# ── Conversation management ────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ai_conversations(request):
    subject = request.GET.get('subject', '')
    qs = AIConversation.objects.filter(user=request.user)
    if subject:
        qs = qs.filter(subject=subject.upper())
    qs = qs.order_by('-updated_at')[:50]
    return Response([{
        'id': c.id,
        'subject': c.subject,
        'section': c.section,
        'title': c.title,
        'updated_at': c.updated_at,
        'message_count': c.messages.count(),
    } for c in qs])


@api_view(['GET', 'DELETE'])
@permission_classes([IsAuthenticated])
def ai_conversation_detail(request, conv_id):
    conv = get_object_or_404(AIConversation, id=conv_id, user=request.user)
    if request.method == 'DELETE':
        conv.delete()
        return Response({'deleted': True})

    messages = conv.messages.order_by('created_at')
    return Response({
        'id': conv.id,
        'subject': conv.subject,
        'section': conv.section,
        'title': conv.title,
        'created_at': conv.created_at,
        'messages': [{
            'id': m.id,
            'role': m.role,
            'content': m.content,
            'image': request.build_absolute_uri(m.image.url) if m.image else None,
            'created_at': m.created_at,
        } for m in messages],
    })


# ── Admin: Structure CRUD ──────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser, JSONParser])
def admin_ai_structures(request):
    if request.method == 'GET':
        subject = request.GET.get('subject', '')
        section = request.GET.get('section', '')
        qs = AIStructure.objects.all()
        if subject:
            qs = qs.filter(subject=subject.upper())
        if section:
            qs = qs.filter(section=section)
        return Response([_structure_data(s, request) for s in qs])

    # POST — create
    data = request.data
    s = AIStructure.objects.create(
        subject=data.get('subject', 'SAT').upper(),
        section=data.get('section', 'general'),
        title=data.get('title', ''),
        content=data.get('content', ''),
        is_active=data.get('is_active', True),
    )
    if request.FILES.get('image'):
        s.image = request.FILES['image']
        s.save(update_fields=['image'])
    return Response(_structure_data(s, request), status=201)


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAdminUser])
@parser_classes([MultiPartParser, JSONParser])
def admin_ai_structure_detail(request, pk):
    s = get_object_or_404(AIStructure, pk=pk)
    if request.method == 'GET':
        return Response(_structure_data(s, request))
    if request.method == 'DELETE':
        s.delete()
        return Response({'deleted': True})
    # PUT
    data = request.data
    s.subject = data.get('subject', s.subject).upper()
    s.section = data.get('section', s.section)
    s.title = data.get('title', s.title)
    s.content = data.get('content', s.content)
    s.is_active = data.get('is_active', s.is_active)
    if request.FILES.get('image'):
        s.image = request.FILES['image']
    s.save()
    return Response(_structure_data(s, request))


def _structure_data(s, request=None):
    return {
        'id': s.id,
        'subject': s.subject,
        'section': s.section,
        'title': s.title,
        'content': s.content,
        'image': (request.build_absolute_uri(s.image.url) if s.image and request else
                  s.image.url if s.image else None),
        'is_active': s.is_active,
        'created_at': s.created_at,
    }
