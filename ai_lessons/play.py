"""
What the lesson player receives, the same for a student and for an admin's preview.

Questions asked while the lesson plays carry the id of the step they follow; the
final test follows the last step, whichever step that is after editing. Correct
answers only ever travel inside an answer that has been given.
"""
from django.db.models import Q


def lesson_play_data(lesson, request):
    """Everything the board player needs for one lesson."""
    return {
        'id': lesson.id,
        'title': lesson.title,
        'subject': lesson.subject,
        'section': lesson.section,
        'language': lesson.language,
        'level': lesson.level,
        'steps': [
            {
                'id': s.id,
                'say': s.say,
                'board': s.board,
                'think': s.think,
                'audio': request.build_absolute_uri(s.audio.url) if s.audio else None,
                'audio_seconds': s.audio_seconds,
            }
            for s in lesson.steps.all()
        ],
    }


def answer_data(answer, question):
    return {
        'choice': answer.choice,
        'text': answer.text,
        'is_correct': answer.is_correct,
        'verdict': answer.verdict,          # written answers: correct | partly | wrong | '' (not marked)
        'feedback': answer.feedback,
        'correct': question.correct,
        'accepted': question.accepted,
        'explanation': question.explanation,
    }


def question_data(question, answer=None, last_step=None):
    return {
        'id': question.id,
        'step': last_step if question.is_quiz else question.step_id,
        'quiz': question.is_quiz,
        'kind': question.kind,
        'prompt': question.prompt,
        'options': question.options,
        'answer': answer_data(answer, question) if answer else None,
    }


def visible_questions(questions):
    """Every question a student can reach: after a step, in the final test or in the homework.
    `questions` is a lesson's questions or LessonQuestion.objects."""
    return questions.filter(Q(is_homework=True) | Q(is_quiz=True) | Q(step__isnull=False))


def asked_in_lesson(questions, last_step):
    """The questions the player stops for: the ones after steps first, then the final test."""
    playing = [q for q in questions if not q.is_homework and (q.step_id or (q.is_quiz and last_step))]
    return sorted(playing, key=lambda q: (q.is_quiz, q.order, q.id))
