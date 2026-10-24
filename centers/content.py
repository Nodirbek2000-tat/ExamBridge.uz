"""
Who sees which tests.

A test with `center = None` is an ExamBridge test; a test with a center is
that learning center's own test.

  ordinary user            ExamBridge tests only
  center student           its center's tests, plus ExamBridge tests unless
                           the director switched "show site mocks" off
  center director/admin/   its center's tests and ExamBridge tests
  teacher

`visible_q(user)` is the one filter every student-facing list and start
endpoint uses. Center imports reuse the admin importers; while one runs,
`importing_for(center)` makes every test it creates belong to that center
(a pre_save hook, see CentersConfig.ready).
"""
from contextlib import contextmanager
from contextvars import ContextVar

from django.db.models import Q

_IMPORT_CENTER = ContextVar('center_import', default=None)


def content_models():
    from cefr.models import CEFRListeningSection, CEFRReadingPassage, CEFRSpeakingTest, CEFRTest, CEFRWritingTest
    from ielts.models import IELTSTest, ListeningSection, ReadingPassage, SpeakingTask, WritingTask
    return (IELTSTest, ReadingPassage, ListeningSection, WritingTask, SpeakingTask,
            CEFRTest, CEFRReadingPassage, CEFRListeningSection, CEFRWritingTest, CEFRSpeakingTest)


def scope(user):
    """(see ExamBridge tests?, ids of centers whose tests this user sees)."""
    if not getattr(user, 'is_authenticated', False):
        return True, ()
    cached = getattr(user, '_content_scope', None)
    if cached is not None:
        return cached
    from .models import CenterMembership
    rows = list(CenterMembership.objects.filter(user=user, is_active=True, center__is_active=True)
                .values_list('center_id', 'role', 'center__show_site_mocks'))
    centers = tuple(sorted({cid for cid, _, _ in rows}))
    student_switches = [show for _, role, show in rows if role == 'student']
    # A student sees ExamBridge tests only while every center they study at allows it
    show_site = all(student_switches) if student_switches else True
    user._content_scope = (show_site, centers)
    return user._content_scope


def visible_q(user, field='center'):
    show_site, centers = scope(user)
    q = Q(**{f'{field}__in': centers}) if centers else Q(pk__in=[])
    if show_site:
        q |= Q(**{f'{field}__isnull': True})
    return q


def visible(user, model):
    """`model.objects` narrowed to what `user` may see."""
    return model.objects.filter(visible_q(user))


def center_visible_q(center, field='center'):
    """What a center hands out to its students (teachers' task pickers)."""
    q = Q(**{field: center})
    if center.show_site_mocks:
        q |= Q(**{f'{field}__isnull': True})
    return q


@contextmanager
def importing_for(center):
    token = _IMPORT_CENTER.set(center.id)
    try:
        yield
    finally:
        _IMPORT_CENTER.reset(token)


def stamp_center(sender, instance, **kwargs):
    """pre_save: tests created during a center import belong to that center."""
    center_id = _IMPORT_CENTER.get()
    if center_id and instance._state.adding and not instance.center_id:
        instance.center_id = center_id
