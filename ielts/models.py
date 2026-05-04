from django.db import models
from django.conf import settings
from django.utils import timezone


class IELTSTest(models.Model):
    class TestType(models.TextChoices):
        ACADEMIC = 'ACADEMIC', 'Academic'
        GENERAL = 'GENERAL', 'General Training'
        FULL_MOCK = 'FULL_MOCK', 'Full Mock Test'

    title = models.CharField(max_length=200)
    test_type = models.CharField(max_length=20, choices=TestType.choices, default=TestType.ACADEMIC)
    description = models.TextField(blank=True)
    is_premium = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Unified audio for full mock tests (all sections use this single audio track)
    audio_file = models.FileField(upload_to='ielts/audio/tests/', blank=True)
    audio_url = models.URLField(max_length=500, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'IELTS Test'

    def __str__(self):
        return f"{self.title} ({self.get_test_type_display()})"


# ── READING ──────────────────────────────────────────────────────────────────

class ReadingPassage(models.Model):
    class Difficulty(models.TextChoices):
        EASY = 'EASY', 'Easy'
        MEDIUM = 'MEDIUM', 'Medium'
        HARD = 'HARD', 'Hard'

    test = models.ForeignKey(IELTSTest, on_delete=models.CASCADE, related_name='passages', null=True, blank=True)
    title = models.CharField(max_length=300)
    content = models.TextField()
    passage_number = models.PositiveSmallIntegerField(default=1)
    image = models.ImageField(upload_to='ielts/reading/', null=True, blank=True)
    time_limit = models.PositiveSmallIntegerField(default=20, help_text='Minutes')
    difficulty = models.CharField(max_length=10, choices=Difficulty.choices, default=Difficulty.MEDIUM)
    is_standalone = models.BooleanField(default=False, help_text='Practice passage (not part of full test)')
    is_premium = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['passage_number']

    def __str__(self):
        return f"Passage {self.passage_number}: {self.title}"


class ReadingQuestion(models.Model):
    class QuestionType(models.TextChoices):
        MCQ              = 'MCQ',   'Multiple Choice'
        TRUE_FALSE_NG    = 'TFNG',  'True/False/Not Given'
        YES_NO_NG        = 'YNNG',  'Yes/No/Not Given'
        GAP_FILL         = 'GAP',   'Gap Filling'
        MATCHING         = 'MATCH', 'Matching Headings'
        MATCHING_INFO    = 'MINFO', 'Matching Information'
        MATCHING_FEAT    = 'MFEAT', 'Matching Features'
        MATCHING_END     = 'MEND',  'Matching Sentence Endings'
        SHORT_ANSWER     = 'SHORT', 'Short Answer'
        SENTENCE_COMP    = 'SENT',  'Sentence Completion'
        MULTI_SELECT     = 'MULTI', 'Multiple Select'
        TABLE_FILL       = 'TABLE', 'Table Completion'
        SUMMARY_FILL     = 'SUMM',  'Summary Completion'
        NOTES_FILL       = 'NOTE',  'Notes / Diagram Completion'
        FLOW_FILL        = 'FLOW',  'Flowchart Completion'
        DIAGRAM_LABEL    = 'MAP',   'Diagram / Map Labelling'

    passage = models.ForeignKey(ReadingPassage, on_delete=models.CASCADE, related_name='questions')
    number = models.PositiveSmallIntegerField()
    question_type = models.CharField(max_length=10, choices=QuestionType.choices)
    content = models.TextField(
        help_text='Question text. For GAP/TABLE: use ___ for blank. For MATCH: the statement to match.'
    )
    image = models.ImageField(upload_to='ielts/questions/', null=True, blank=True)
    # For MULTI_SELECT / MATCHING_INFO: pipe-separated e.g. "A|C|E"
    correct_answer = models.CharField(max_length=500)
    explanation = models.TextField(blank=True)

    # Group instruction shown once above a block of questions
    group_instruction = models.TextField(
        blank=True,
        help_text='e.g. "Questions 14-16: Choose THREE answers from A-K"'
    )
    # How many options to select for MULTI_SELECT
    max_selections = models.PositiveSmallIntegerField(
        default=1,
        help_text='For MULTI_SELECT: how many answers student must select'
    )
    # Word bank for SUMM/NOTE type (list of words shown to student)
    word_bank = models.JSONField(default=list, blank=True, help_text='Word bank for SUMM type drag-and-drop')
    # Answer review: passage evidence shown in review mode
    answer_review = models.TextField(blank=True, help_text='Passage evidence shown in yellow box during review mode')

    class Meta:
        ordering = ['number']
        unique_together = ['passage', 'number']

    def __str__(self):
        return f"Q{self.number} ({self.get_question_type_display()}) - {self.passage}"

    def correct_answers_list(self):
        """Returns list of correct answers (handles pipe-separated multi-select)."""
        return [a.strip().upper() for a in self.correct_answer.split('|') if a.strip()]


class ReadingChoice(models.Model):
    question = models.ForeignKey(ReadingQuestion, on_delete=models.CASCADE, related_name='choices')
    option = models.CharField(max_length=5)  # A, B, C … or longer for matching
    text = models.TextField()

    class Meta:
        ordering = ['option']


# ── LISTENING ────────────────────────────────────────────────────────────────

class ListeningSection(models.Model):
    class Difficulty(models.TextChoices):
        EASY = 'EASY', 'Easy'
        MEDIUM = 'MEDIUM', 'Medium'
        HARD = 'HARD', 'Hard'

    test = models.ForeignKey(IELTSTest, on_delete=models.CASCADE, related_name='listening_sections', null=True, blank=True)
    section_number = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=200)
    audio_file = models.FileField(upload_to='ielts/audio/', blank=True)
    audio_url = models.URLField(max_length=500, blank=True, help_text='External audio URL')
    transcript = models.TextField(
        blank=True,
        help_text='Full transcript. Use [1], [2] markers to show where answers appear in text.'
    )
    difficulty = models.CharField(max_length=10, choices=Difficulty.choices, default=Difficulty.MEDIUM)
    is_standalone = models.BooleanField(default=False)
    is_premium = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['section_number']

    def __str__(self):
        return f"Section {self.section_number}: {self.title}"


class ListeningQuestion(models.Model):
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
        LABEL_MAP     = 'MAP',   'Map / Plan Labelling'

    section = models.ForeignKey(ListeningSection, on_delete=models.CASCADE, related_name='questions')
    number = models.PositiveSmallIntegerField()
    question_type = models.CharField(max_length=10, choices=QuestionType.choices)
    content = models.TextField(
        help_text='Question/prompt text. For GAP: text with ___ blank. For TABLE: cell label.'
    )
    image = models.ImageField(upload_to='ielts/listening/', null=True, blank=True)
    # Pipe-separated for MULTI_SELECT e.g. "A|C"
    correct_answer = models.CharField(max_length=500)
    explanation = models.TextField(blank=True)

    # Group instruction shown once above a block of questions
    group_instruction = models.TextField(
        blank=True,
        help_text='e.g. "Questions 22-25: Choose THREE letters A-G"'
    )
    max_selections = models.PositiveSmallIntegerField(
        default=1,
        help_text='For MULTI_SELECT: how many answers student must select'
    )
    word_bank = models.JSONField(default=list, blank=True, help_text='Word bank for SUMM type')
    answer_review = models.TextField(blank=True, help_text='Evidence shown in review mode')

    class Meta:
        ordering = ['number']

    def __str__(self):
        return f"Q{self.number} ({self.get_question_type_display()}) - {self.section}"

    def correct_answers_list(self):
        return [a.strip().upper() for a in self.correct_answer.split('|') if a.strip()]


class ListeningChoice(models.Model):
    question = models.ForeignKey(ListeningQuestion, on_delete=models.CASCADE, related_name='choices')
    option = models.CharField(max_length=5)
    text = models.TextField()

    class Meta:
        ordering = ['option']


# ── SPEAKING ────────────────────────────────────────────────────────────────

class SpeakingTask(models.Model):
    class Part(models.IntegerChoices):
        PART1 = 1, 'Part 1 — Introduction'
        PART2 = 2, 'Part 2 — Long Turn'
        PART3 = 3, 'Part 3 — Discussion'

    title = models.CharField(max_length=200)
    part = models.IntegerField(choices=Part.choices, default=1)
    prompt = models.TextField(blank=True)
    follow_up_questions = models.JSONField(default=list)
    prep_time = models.PositiveSmallIntegerField(default=60, help_text='Prep time in seconds (Part 2 only)')
    speak_time = models.PositiveSmallIntegerField(default=120, help_text='Speaking time limit in seconds')
    is_premium = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # Extended fields
    test_type = models.CharField(max_length=10, choices=[('PART', 'Single Part'), ('MOCK', 'Full Mock')], default='PART')
    topic = models.CharField(max_length=200, blank=True)
    questions = models.JSONField(default=list, blank=True, help_text='List of question strings for Part 1 and Part 3')
    bullet_points = models.JSONField(default=list, blank=True, help_text='Bullet points for Part 2 cue card')
    follow_up = models.CharField(max_length=500, blank=True, help_text='Follow-up question after Part 2 long turn')
    parts_data = models.JSONField(default=list, blank=True, help_text='Full mock: list of part objects')

    class Meta:
        ordering = ['part', 'title']

    def __str__(self):
        return f"Part {self.part}: {self.title}"


# ── WRITING ─────────────────────────────────────────────────────────────────

class WritingTask(models.Model):
    class TaskType(models.IntegerChoices):
        TASK1 = 1, 'Task 1'
        TASK2 = 2, 'Task 2 (Essay)'

    class TestType(models.TextChoices):
        ACADEMIC = 'ACADEMIC', 'Academic'
        GENERAL = 'GENERAL', 'General Training'

    class Difficulty(models.TextChoices):
        EASY   = 'EASY',   'Easy'
        MEDIUM = 'MEDIUM', 'Medium'
        HARD   = 'HARD',   'Hard'

    title        = models.CharField(max_length=200)
    task_type    = models.IntegerField(choices=TaskType.choices)
    test_type    = models.CharField(max_length=10, choices=TestType.choices, default=TestType.ACADEMIC)
    difficulty   = models.CharField(max_length=10, choices=Difficulty.choices, default=Difficulty.MEDIUM)
    prompt       = models.TextField()
    image        = models.ImageField(upload_to='ielts/writing/', null=True, blank=True, help_text='Chart/graph for Task 1')
    recommendations = models.JSONField(default=list, blank=True,
                                       help_text='List of vocabulary/tip words shown below the prompt')
    min_words    = models.PositiveSmallIntegerField(default=150)
    time_limit   = models.PositiveSmallIntegerField(default=20, help_text='Minutes')
    sample_answer= models.TextField(blank=True)
    is_premium   = models.BooleanField(default=False)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Task {self.task_type}: {self.title}"


# ── ATTEMPTS ────────────────────────────────────────────────────────────────

class IELTSAttempt(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
        COMPLETED = 'COMPLETED', 'Completed'
        ABANDONED = 'ABANDONED', 'Abandoned'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ielts_attempts')
    test = models.ForeignKey(IELTSTest, on_delete=models.CASCADE, related_name='attempts', null=True, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.IN_PROGRESS)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # For standalone practice tracking
    reading_passage = models.ForeignKey(
        ReadingPassage, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    listening_section = models.ForeignKey(
        ListeningSection, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

    # Scores (0.0 - 9.0 band)
    reading_band = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    listening_band = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    speaking_band = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    writing_band = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    overall_band = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)

    # Security / exam integrity
    tab_switches = models.PositiveSmallIntegerField(default=0)
    fullscreen_exits = models.PositiveSmallIntegerField(default=0)
    security_events = models.JSONField(default=list)

    class Meta:
        ordering = ['-started_at']

    def complete(self):
        self.status = self.Status.COMPLETED
        self.finished_at = timezone.now()
        self.save(update_fields=['status', 'finished_at'])


class ReadingAnswer(models.Model):
    attempt = models.ForeignKey(IELTSAttempt, on_delete=models.CASCADE, related_name='reading_answers')
    question = models.ForeignKey(ReadingQuestion, on_delete=models.CASCADE)
    answer = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)
    time_spent = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ['attempt', 'question']


class ListeningAnswer(models.Model):
    attempt = models.ForeignKey(IELTSAttempt, on_delete=models.CASCADE, related_name='listening_answers')
    question = models.ForeignKey(ListeningQuestion, on_delete=models.CASCADE)
    answer = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)

    class Meta:
        unique_together = ['attempt', 'question']


class SpeakingResponse(models.Model):
    attempt = models.ForeignKey(IELTSAttempt, on_delete=models.CASCADE, related_name='speaking_responses')
    task = models.ForeignKey(SpeakingTask, on_delete=models.CASCADE)
    audio_file = models.FileField(upload_to='ielts/responses/speaking/', blank=True, null=True)
    transcript = models.TextField(blank=True)
    ai_feedback = models.TextField(blank=True)
    ai_band = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    transcripts = models.JSONField(default=list, blank=True, help_text='[{question, transcript}, ...]')
    ai_criteria = models.JSONField(default=dict, blank=True)


class WritingResponse(models.Model):
    attempt = models.ForeignKey(IELTSAttempt, on_delete=models.CASCADE, related_name='writing_responses')
    task = models.ForeignKey(WritingTask, on_delete=models.CASCADE)
    response_text = models.TextField()
    word_count = models.PositiveSmallIntegerField(default=0)
    ai_feedback = models.TextField(blank=True)
    ai_band = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    ai_criteria = models.JSONField(default=dict, help_text='TA/CC/LR/GRA scores')
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.response_text:
            self.word_count = len(self.response_text.split())
        super().save(*args, **kwargs)


# ── BOOKMARKS ────────────────────────────────────────────────────────────────

class BookmarkedQuestion(models.Model):
    class SourceType(models.TextChoices):
        IELTS_READING = 'IELTS_READING', 'IELTS Reading'
        IELTS_LISTENING = 'IELTS_LISTENING', 'IELTS Listening'
        CEFR = 'CEFR', 'CEFR'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bookmarks')
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    question_id = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ['user', 'source_type', 'question_id']

    def __str__(self):
        return f"{self.user} bookmarked {self.source_type} Q{self.question_id}"
