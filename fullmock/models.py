"""
Full mock exams: one real-exam rehearsal made of the four sections in exam
order — Listening, Reading, Writing, Speaking.

A FullMock does not hold questions itself. Each section points to a test
that was imported on its own (IELTS reading mock, CEFR writing test, …), so
the existing exam screens run every section and save results as usual.
A FullMockAttempt remembers which section attempt belongs to the rehearsal
and the result is read back from those attempts.
"""
from django.conf import settings
from django.db import models


class FullMock(models.Model):
    IELTS = 'ielts'
    CEFR = 'cefr'
    EXAM_CHOICES = [(IELTS, 'IELTS'), (CEFR, 'CEFR')]

    exam = models.CharField(max_length=8, choices=EXAM_CHOICES)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    # IELTS: Listening → Reading → Writing Task 1 + Task 2 → Speaking
    ielts_listening = models.ForeignKey('ielts.IELTSTest', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='+')
    ielts_reading = models.ForeignKey('ielts.IELTSTest', on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='+')
    ielts_writing1 = models.ForeignKey('ielts.WritingTask', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    ielts_writing2 = models.ForeignKey('ielts.WritingTask', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    ielts_speaking = models.ForeignKey('ielts.SpeakingTask', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')

    # CEFR multilevel: Listening → Reading → Writing → Speaking
    cefr_listening = models.ForeignKey('cefr.CEFRTest', on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='+')
    cefr_reading = models.ForeignKey('cefr.CEFRTest', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+')
    cefr_writing = models.ForeignKey('cefr.CEFRWritingTest', on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='+')
    cefr_speaking = models.ForeignKey('cefr.CEFRSpeakingTest', on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='+')

    is_published = models.BooleanField(default=False, help_text='Students see it only when published and complete')
    is_premium = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_exam_display()} · {self.title}'


class FullMockAttempt(models.Model):
    IN_PROGRESS = 'IN_PROGRESS'
    COMPLETED = 'COMPLETED'

    mock = models.ForeignKey(FullMock, on_delete=models.CASCADE, related_name='attempts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='full_mock_attempts')
    status = models.CharField(max_length=12, default=IN_PROGRESS,
                              choices=[(IN_PROGRESS, 'In progress'), (COMPLETED, 'Completed')])
    # {"listening": 12, "reading": 13, ...} — the section attempt / response ids
    steps = models.JSONField(default=dict, blank=True)
    # Cached final result once every section is scored
    result = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-started_at']
        indexes = [models.Index(fields=['user', 'status'])]

    def __str__(self):
        return f'{self.user} · {self.mock} · {self.status}'
