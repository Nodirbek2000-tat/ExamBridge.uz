"""
Turns a lesson's sources into plain text for the prompt.

Links are named but never fetched: the server does not visit addresses typed into
a form. Images are skipped for now. Whatever could not be used is reported, so the
admin knows what the AI did not see.
"""
import io
import logging
import os

from ai_chat.models import AIStructure

from .models import LessonSource

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 30_000         # keeps every prompt, and so every lesson, affordable
MAX_STRUCTURES_CHARS = 6_000
MAX_PDF_PAGES = 60
TEXT_EXTENSIONS = {'.txt', '.md'}


def _pdf_text(data):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return '\n'.join((page.extract_text() or '') for page in reader.pages[:MAX_PDF_PAGES])


def _docx_text(data):
    from docx import Document
    return '\n'.join(p.text for p in Document(io.BytesIO(data)).paragraphs)


def _file_text(source):
    """The text of an uploaded file. '' when it holds none we can read."""
    ext = os.path.splitext(source.file.name)[1].lower()
    if ext not in TEXT_EXTENSIONS | {'.pdf', '.docx'}:
        return ''
    with source.file.open('rb') as handle:
        data = handle.read()
    if ext in TEXT_EXTENSIONS:
        return data.decode('utf-8', errors='replace')
    return _pdf_text(data) if ext == '.pdf' else _docx_text(data)


def lesson_context(lesson):
    """(material for the prompt, titles of the sources that could not be used)."""
    parts, skipped = [], []
    room = MAX_CONTEXT_CHARS

    for source in lesson.sources.all():
        if source.kind == LessonSource.Kind.LINK:
            skipped.append(source.title)
            continue
        try:
            text = source.text if source.kind == LessonSource.Kind.TEXT else _file_text(source)
        except Exception:
            logger.warning('Could not read lesson source %s', source.id, exc_info=True)
            text = ''
        text = text.strip()
        if not text or room <= 0:
            skipped.append(source.title)
            continue
        parts.append(f'## {source.title}\n{text[:room]}')
        room -= len(text)

    return '\n\n'.join(parts), skipped


def teaching_structures(lesson):
    """The admin's AI Structures that fit this lesson, the ones the AI Tutor follows too."""
    structures = AIStructure.objects.filter(
        subject__in=[lesson.subject, 'GENERAL'], section__in=[lesson.section, 'general'], is_active=True,
    )
    text = '\n\n'.join(f'## {s.title}\n{s.content}' for s in structures)
    return text[:MAX_STRUCTURES_CHARS]
