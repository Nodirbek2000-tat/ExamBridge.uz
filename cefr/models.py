from django.db import models
from django.conf import settings
from django.utils import timezone


class CEFRTest(models.Model):
    class Level(models.TextChoices):
        A1 = 'A1', 'A1 — Beginner'
        A2 = 'A2', 'A2 — Elementary'
        B1 = 'B1', 'B1 — Intermediate'
        B2 = 'B2', 'B2 — Upper-Intermediate'
        C1 = 'C1', 'C1 — Advanced'
        C2 = 'C2', 'C2 — Proficiency'

    class TestType(models.TextChoices):
        FULL = 'FULL', 'Full Practice Test'
        GRAMMAR = 'GRAMMAR', 'Grammar & Vocabulary'
        READING = 'READING', 'Reading Comprehension'
        LISTENING = 'LISTENING', 'Listening'
        WRITING = 'WRITING', 'Writing'

    title = models.CharField(max_length=200)
    level = models.CharField(max_length=2, choices=Level.choices)
    test_type = models.CharField(max_length=10, choices=TestType.choices, default=TestType.FULL)
    description = models.TextField(blank=True)
    time_limit = models.PositiveSmallIntegerField(default=60, help_text='Minutes')
    is_premium = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['level', '-created_at']
        verbose_name = 'CEFR Test'

    def __str__(self):
        return f"[{self.level}] {self.title}"


# ── READING ──────────────────────────────────────────────────────────────────

class CEFRReadingPassage(models.Model):
    class Difficulty(models.TextChoices):
        EASY   = 'EASY',   'Easy'
        MEDIUM = 'MEDIUM', 'Medium'
        HARD   = 'HARD',   'Hard'

    test = models.ForeignKey(CEFRTest, on_delete=models.CASCADE, related_name='reading_passages', null=True, blank=True)
    title = models.CharField(max_length=300)
    content = models.TextField()
    passage_number = models.PositiveSmallIntegerField(default=1)
    level = models.CharField(max_length=2, choices=CEFRTest.Level.choices, blank=True, default='')
    time_limit = models.PositiveSmallIntegerField(default=20, help_text='Minutes')
    difficulty = models.CharField(max_length=6, choices=Difficulty.choices, default=Difficulty.MEDIUM)
    image = models.ImageField(upload_to='cefr/reading/', null=True, blank=True)
    is_standalone = models.BooleanField(default=True)
    is_premium = models.BooleanField(default=False)
    is_mock = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['level', 'passage_number']

    def __str__(self):
        return f"[{self.level}] CEFR Passage: {self.title}"


class CEFRReadingQuestion(models.Model):
    class QuestionType(models.TextChoices):
        MCQ           = 'MCQ',   'Multiple Choice'
        TRUE_FALSE_NG = 'TFNG',  'True/False/Not Given'
        YES_NO_NG     = 'YNNG',  'Yes/No/Not Given'
        GAP_FILL      = 'GAP',   'Gap Filling'
        MATCHING      = 'MATCH', 'Matching Headings'
        MATCHING_INFO = 'MINFO', 'Matching Information'
        MATCHING_FEAT = 'MFEAT', 'Matching Features'
        MATCHING_END  = 'MEND',  'Matching Sentence Endings'
        MULTI_SELECT  = 'MULTI', 'Multiple Select'
        TABLE_FILL    = 'TABLE', 'Table Completion'
        SENTENCE_COMP = 'SENT',  'Sentence Completion'
        SUMMARY_FILL  = 'SUMM',  'Summary Completion'
        NOTES_FILL    = 'NOTE',  'Notes / Diagram Completion'
        FLOW_FILL     = 'FLOW',  'Flowchart Completion'
        SHORT_ANSWER  = 'SHORT', 'Short Answer'

    passage = models.ForeignKey(CEFRReadingPassage, on_delete=models.CASCADE, related_name='questions')
    number = models.PositiveSmallIntegerField()
    question_type = models.CharField(max_length=10, choices=QuestionType.choices)
    content = models.TextField()
    correct_answer = models.CharField(max_length=500)
    explanation = models.TextField(blank=True)
    group_instruction = models.TextField(blank=True)
    max_selections = models.PositiveSmallIntegerField(default=1)
    image = models.ImageField(upload_to='cefr/reading/questions/', null=True, blank=True)
    word_bank = models.JSONField(default=list, blank=True, help_text='Word bank for SUMM type drag-and-drop')
    answer_review = models.TextField(blank=True, help_text='Passage evidence shown in yellow box during review mode')

    class Meta:
        ordering = ['number']
        unique_together = ['passage', 'number']

    def correct_answers_list(self):
        return [a.strip().upper() for a in self.correct_answer.split('|') if a.strip()]


class CEFRReadingChoice(models.Model):
    question = models.ForeignKey(CEFRReadingQuestion, on_delete=models.CASCADE, related_name='choices')
    option = models.CharField(max_length=5)
    text = models.TextField()

    class Meta:
        ordering = ['option']


# ── LISTENING ────────────────────────────────────────────────────────────────

class CEFRListeningSection(models.Model):
    test = models.ForeignKey(CEFRTest, on_delete=models.CASCADE, related_name='listening_sections', null=True, blank=True)
    section_number = models.PositiveSmallIntegerField(default=1)
    title = models.CharField(max_length=200)
    level = models.CharField(max_length=2, choices=CEFRTest.Level.choices, blank=True, default='')
    time_limit = models.PositiveSmallIntegerField(default=25, help_text='Minutes')
    audio_file = models.FileField(upload_to='cefr/audio/', blank=True)
    audio_url = models.URLField(max_length=500, blank=True)
    transcript = models.TextField(
        blank=True,
        help_text='Full transcript. Use [1], [2] markers for answer positions.'
    )
    is_standalone = models.BooleanField(default=True)
    is_mock = models.BooleanField(default=False)
    is_premium = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['level', 'section_number']

    def __str__(self):
        return f"[{self.level}] CEFR Listening: {self.title}"


class CEFRListeningQuestion(models.Model):
    class QuestionType(models.TextChoices):
        MCQ           = 'MCQ',   'Multiple Choice'
        MULTI_SELECT  = 'MULTI', 'Multiple Select'
        TRUE_FALSE_NG = 'TFNG',  'True/False/Not Given'
        YES_NO_NG     = 'YNNG',  'Yes/No/Not Given'
        GAP_FILL      = 'GAP',   'Gap Filling'
        TABLE_FILL    = 'TABLE', 'Table / Form Completion'
        NOTES_FILL    = 'NOTE',  'Notes Completion'
        SUMMARY_FILL  = 'SUMM',  'Summary Completion'
        FLOW_FILL     = 'FLOW',  'Flowchart Completion'
        SENTENCE_COMP = 'SENT',  'Sentence Completion'
        MATCHING      = 'MATCH', 'Matching'
        MATCHING_FEAT = 'MFEAT', 'Matching Features'
        MATCHING_END  = 'MEND',  'Matching Sentence Endings'
        SHORT_ANSWER  = 'SHORT', 'Short Answer'

    section = models.ForeignKey(CEFRListeningSection, on_delete=models.CASCADE, related_name='questions')
    number = models.PositiveSmallIntegerField()
    question_type = models.CharField(max_length=10, choices=QuestionType.choices)
    content = models.TextField()
    correct_answer = models.CharField(max_length=500)
    explanation = models.TextField(blank=True)
    group_instruction = models.TextField(blank=True)
    max_selections = models.PositiveSmallIntegerField(default=1)
    image = models.ImageField(upload_to='cefr/listening/questions/', null=True, blank=True)
    word_bank = models.JSONField(default=list, blank=True, help_text='Word bank for SUMM type drag-and-drop')
    answer_review = models.TextField(blank=True, help_text='Evidence shown in review mode')

    class Meta:
        ordering = ['number']

    def correct_answers_list(self):
        return [a.strip().upper() for a in self.correct_answer.split('|') if a.strip()]


class CEFRListeningChoice(models.Model):
    question = models.ForeignKey(CEFRListeningQuestion, on_delete=models.CASCADE, related_name='choices')
    option = models.CharField(max_length=5)
    text = models.TextField()

    class Meta:
        ordering = ['option']


# ── GRAMMAR / VOCABULARY QUESTIONS ───────────────────────────────────────────

class CEFRQuestion(models.Model):
    class QuestionType(models.TextChoices):
        MCQ              = 'MCQ',   'Multiple Choice'
        GAP_FILL         = 'GAP',   'Gap Filling'
        TRUE_FALSE       = 'TF',    'True/False'
        MATCHING         = 'MATCH', 'Matching'
        ERROR_CORRECTION = 'ERROR', 'Error Correction'
        WORD_FORM        = 'WORD',  'Word Formation'
        TRANSFORMATION   = 'TRANS', 'Sentence Transformation'

    test = models.ForeignKey(CEFRTest, on_delete=models.CASCADE, related_name='questions')
    number = models.PositiveSmallIntegerField()
    question_type = models.CharField(max_length=10, choices=QuestionType.choices)
    content = models.TextField()
    passage = models.TextField(blank=True, help_text='Reading passage if applicable')
    image = models.ImageField(upload_to='cefr/questions/', null=True, blank=True)
    correct_answer = models.CharField(max_length=500)
    explanation = models.TextField(blank=True)
    group_instruction = models.TextField(blank=True)

    class Meta:
        ordering = ['number']
        unique_together = ['test', 'number']

    def __str__(self):
        return f"Q{self.number} [{self.test.level}] - {self.get_question_type_display()}"


class CEFRChoice(models.Model):
    question = models.ForeignKey(CEFRQuestion, on_delete=models.CASCADE, related_name='choices')
    option = models.CharField(max_length=1)
    text = models.TextField()

    class Meta:
        ordering = ['option']


# ── ATTEMPTS ────────────────────────────────────────────────────────────────

class CEFRAttempt(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
        COMPLETED = 'COMPLETED', 'Completed'

    class AttemptType(models.TextChoices):
        GRAMMAR = 'GRAMMAR', 'Grammar/Vocabulary'
        READING = 'READING', 'Reading'
        LISTENING = 'LISTENING', 'Listening'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cefr_attempts')
    test = models.ForeignKey(CEFRTest, on_delete=models.CASCADE, related_name='attempts', null=True, blank=True)
    attempt_type = models.CharField(max_length=15, choices=AttemptType.choices, default=AttemptType.GRAMMAR)

    # For standalone reading/listening practice
    reading_passage = models.ForeignKey(
        CEFRReadingPassage, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    listening_section = models.ForeignKey(
        CEFRListeningSection, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

    status = models.CharField(max_length=15, choices=Status.choices, default=Status.IN_PROGRESS)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    score_percent = models.FloatField(null=True, blank=True)
    correct_count = models.PositiveSmallIntegerField(default=0)
    total_count = models.PositiveSmallIntegerField(default=0)
    level_achieved = models.CharField(max_length=2, blank=True)

    tab_switches = models.PositiveSmallIntegerField(default=0)
    security_events = models.JSONField(default=list)

    class Meta:
        ordering = ['-started_at']

    def complete(self):
        self.status = self.Status.COMPLETED
        self.finished_at = timezone.now()
        self.save(update_fields=['status', 'finished_at'])


class CEFRAnswer(models.Model):
    attempt = models.ForeignKey(CEFRAttempt, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(CEFRQuestion, on_delete=models.CASCADE)
    answer = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)

    class Meta:
        unique_together = ['attempt', 'question']


class CEFRReadingAnswer(models.Model):
    attempt = models.ForeignKey(CEFRAttempt, on_delete=models.CASCADE, related_name='reading_answers')
    question = models.ForeignKey(CEFRReadingQuestion, on_delete=models.CASCADE)
    answer = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)

    class Meta:
        unique_together = ['attempt', 'question']


class CEFRListeningAnswer(models.Model):
    attempt = models.ForeignKey(CEFRAttempt, on_delete=models.CASCADE, related_name='listening_answers')
    question = models.ForeignKey(CEFRListeningQuestion, on_delete=models.CASCADE)
    answer = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)

    class Meta:
        unique_together = ['attempt', 'question']
