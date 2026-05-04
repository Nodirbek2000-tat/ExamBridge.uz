from django.db import models
from django.conf import settings
from django.utils import timezone


class Test(models.Model):
    """SAT Test — organized by year, month, form."""

    class TestType(models.TextChoices):
        SAT = 'SAT', 'SAT'
        PSAT = 'PSAT', 'PSAT'

    class Month(models.IntegerChoices):
        JANUARY = 1, 'January'
        FEBRUARY = 2, 'February'
        MARCH = 3, 'March'
        APRIL = 4, 'April'
        MAY = 5, 'May'
        JUNE = 6, 'June'
        JULY = 7, 'July'
        AUGUST = 8, 'August'
        SEPTEMBER = 9, 'September'
        OCTOBER = 10, 'October'
        NOVEMBER = 11, 'November'
        DECEMBER = 12, 'December'

    class TestMode(models.TextChoices):
        FULL = 'FULL', 'Full Test (Adaptive)'
        INDIVIDUAL = 'INDIVIDUAL', 'Individual Module'

    test_type = models.CharField(max_length=10, choices=TestType.choices, default=TestType.SAT)
    test_mode = models.CharField(max_length=15, choices=TestMode.choices, default=TestMode.FULL,
                                  help_text='FULL = adaptive M2 (easy/medium/hard), INDIVIDUAL = standard M2 only')
    year = models.PositiveSmallIntegerField()
    month = models.IntegerField(choices=Month.choices)
    form = models.CharField(max_length=5, default='A', help_text='Form letter: A, B, C, D...')
    is_international = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_premium = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-year', '-month']
        unique_together = ['test_type', 'year', 'month', 'form', 'is_international']
        verbose_name = 'Test'
        verbose_name_plural = 'Tests'

    def __str__(self):
        region = 'INTL' if self.is_international else 'US'
        return f"{self.test_type} {self.get_month_display()} {self.year} Form {self.form} ({region})"

    @property
    def display_name(self):
        return self.__str__()


class TestSection(models.Model):
    """English or Math section of a test."""

    class SectionType(models.TextChoices):
        ENGLISH = 'ENGLISH', 'Reading & Writing'
        MATH = 'MATH', 'Math'

    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name='sections')
    section_type = models.CharField(max_length=10, choices=SectionType.choices)

    class Meta:
        unique_together = ['test', 'section_type']
        verbose_name = 'Test Section'
        verbose_name_plural = 'Test Sections'

    def __str__(self):
        return f"{self.test} - {self.get_section_type_display()}"


class Module(models.Model):
    """Module 1 or Module 2 of a section."""

    class DifficultyVariant(models.TextChoices):
        STANDARD = 'STANDARD', 'Standard'
        EASY = 'EASY', 'Easy (Adaptive)'
        MEDIUM = 'MEDIUM', 'Medium (Adaptive)'
        HARD = 'HARD', 'Hard (Adaptive)'

    section = models.ForeignKey(TestSection, on_delete=models.CASCADE, related_name='modules')
    module_number = models.PositiveSmallIntegerField(choices=[(1, 'Module 1'), (2, 'Module 2')])
    difficulty_variant = models.CharField(
        max_length=10, choices=DifficultyVariant.choices, default=DifficultyVariant.STANDARD,
        help_text='For Module 2: EASY or HARD variant (adaptive routing)'
    )
    # time in minutes: Math=35, English=32
    time_limit = models.PositiveSmallIntegerField(default=35)

    class Meta:
        unique_together = ['section', 'module_number', 'difficulty_variant']
        ordering = ['module_number']
        verbose_name = 'Module'
        verbose_name_plural = 'Modules'

    def __str__(self):
        return f"{self.section} - Module {self.module_number}"

    @property
    def question_count(self):
        return self.questions.count()


class Question(models.Model):
    """Individual SAT question."""

    class QuestionType(models.TextChoices):
        MULTIPLE_CHOICE = 'MCQ', 'Multiple Choice'
        INPUT = 'INPUT', 'Student-Produced Response'

    class Difficulty(models.TextChoices):
        EASY = 'EASY', 'Easy'
        MEDIUM = 'MEDIUM', 'Medium'
        HARD = 'HARD', 'Hard'

    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name='questions')
    number = models.PositiveSmallIntegerField()
    question_type = models.CharField(max_length=10, choices=QuestionType.choices, default=QuestionType.MULTIPLE_CHOICE)

    # Content — supports HTML + LaTeX (MathJax)
    content = models.TextField(help_text='Supports HTML and LaTeX math (\\( \\) for inline, \\[ \\] for block)')
    math_equation = models.TextField(blank=True, help_text='LaTeX equation shown centered above content, e.g. \\( ax^2+bx+c=0 \\)')
    image = models.ImageField(upload_to='questions/', null=True, blank=True)
    table_data = models.JSONField(null=True, blank=True, help_text='Table data as JSON array of rows')
    passage = models.TextField(blank=True, help_text='Reading passage for English questions')

    # Answer
    correct_answer = models.CharField(max_length=500)
    explanation = models.TextField(blank=True)

    difficulty = models.CharField(max_length=10, choices=Difficulty.choices, default=Difficulty.MEDIUM)

    # SAT content domain (for results breakdown)
    # English: information_and_ideas | craft_and_structure | expression_of_ideas | standard_english
    # Math:    algebra | advanced_math | problem_data | geometry
    category = models.CharField(max_length=100, blank=True, help_text='SAT content domain category')
    topic = models.CharField(max_length=200, blank=True, help_text='Sub-topic within the category')

    class Meta:
        ordering = ['number']
        unique_together = ['module', 'number']
        verbose_name = 'Question'
        verbose_name_plural = 'Questions'
        indexes = [
            models.Index(fields=['module', 'number']),
            models.Index(fields=['difficulty']),
        ]

    def __str__(self):
        return f"Q{self.number} - {self.module}"


class Choice(models.Model):
    """Answer choice for MCQ questions."""

    OPTION_CHOICES = [('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D')]

    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='choices')
    option = models.CharField(max_length=1, choices=OPTION_CHOICES)
    text = models.TextField()
    image = models.ImageField(upload_to='choices/', null=True, blank=True)

    class Meta:
        ordering = ['option']
        unique_together = ['question', 'option']
        verbose_name = 'Choice'
        verbose_name_plural = 'Choices'

    def __str__(self):
        return f"{self.question} - {self.option}"


# ─── TEST ATTEMPT ─────────────────────────────────────────────────────────────

class TestAttempt(models.Model):
    """User's attempt at a full test."""

    class Status(models.TextChoices):
        IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
        COMPLETED = 'COMPLETED', 'Completed'
        ABANDONED = 'ABANDONED', 'Abandoned'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='attempts')
    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name='attempts')
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.IN_PROGRESS)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # Track current position
    current_module = models.ForeignKey(Module, on_delete=models.SET_NULL, null=True, blank=True)
    current_question_number = models.PositiveSmallIntegerField(default=1)
    is_individual = models.BooleanField(default=False, help_text='Individual module attempt (not full test)')

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['user', 'test']),
        ]
        verbose_name = 'Test Attempt'
        verbose_name_plural = 'Test Attempts'

    def __str__(self):
        return f"{self.user.email} - {self.test} - {self.status}"

    def complete(self):
        self.status = self.Status.COMPLETED
        self.finished_at = timezone.now()
        self.save(update_fields=['status', 'finished_at'])


class Answer(models.Model):
    """User's answer to a single question."""

    attempt = models.ForeignKey(TestAttempt, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='answers')
    selected_choice = models.ForeignKey(Choice, on_delete=models.SET_NULL, null=True, blank=True)
    text_answer = models.CharField(max_length=200, blank=True)  # for INPUT type
    is_correct = models.BooleanField(default=False)
    is_bookmarked = models.BooleanField(default=False)
    is_skipped = models.BooleanField(default=False)
    time_spent = models.PositiveIntegerField(default=0, help_text='Seconds spent on this question')
    answered_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['attempt', 'question']
        indexes = [
            models.Index(fields=['attempt', 'is_correct']),
            models.Index(fields=['attempt', 'is_bookmarked']),
        ]
        verbose_name = 'Answer'
        verbose_name_plural = 'Answers'

    def __str__(self):
        return f"{self.attempt} - Q{self.question.number}"

    def save(self, *args, **kwargs):
        # Auto-check correctness
        if self.question.question_type == 'MCQ' and self.selected_choice:
            self.is_correct = (self.selected_choice.option == self.question.correct_answer)
        elif self.question.question_type == 'INPUT' and self.text_answer:
            self.is_correct = (self.text_answer.strip().lower() == self.question.correct_answer.strip().lower())
        super().save(*args, **kwargs)


# ─── RESULTS ─────────────────────────────────────────────────────────────────

class TestResult(models.Model):
    """Final calculated result for a completed attempt."""

    attempt = models.OneToOneField(TestAttempt, on_delete=models.CASCADE, related_name='result')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='results')

    # Scaled scores (SAT scale)
    math_score = models.PositiveSmallIntegerField(default=0)      # 200-800
    english_score = models.PositiveSmallIntegerField(default=0)   # 200-800
    total_score = models.PositiveSmallIntegerField(default=0)     # 400-1600

    # Raw scores
    math_raw = models.PositiveSmallIntegerField(default=0)
    english_raw = models.PositiveSmallIntegerField(default=0)

    # Per-module breakdown
    math_m1_correct = models.PositiveSmallIntegerField(default=0)
    math_m2_correct = models.PositiveSmallIntegerField(default=0)
    english_m1_correct = models.PositiveSmallIntegerField(default=0)
    english_m2_correct = models.PositiveSmallIntegerField(default=0)

    calculated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-calculated_at']
        indexes = [
            models.Index(fields=['user', 'total_score']),
            models.Index(fields=['user', 'calculated_at']),
        ]
        verbose_name = 'Test Result'
        verbose_name_plural = 'Test Results'

    def __str__(self):
        return f"{self.user.email} - {self.total_score} ({self.attempt.test})"


class AIAnalysis(models.Model):
    """AI-generated analysis for a test result."""

    result = models.OneToOneField(TestResult, on_delete=models.CASCADE, related_name='ai_analysis')
    weak_areas = models.JSONField(default=list)
    strong_areas = models.JSONField(default=list)
    recommendations = models.TextField()
    university_suggestions = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'AI Analysis'
        verbose_name_plural = 'AI Analyses'

    def __str__(self):
        return f"AI Analysis - {self.result}"


# ─── SAVED QUESTIONS ─────────────────────────────────────────────────────────

class SavedQuestion(models.Model):
    """User can save/bookmark questions for later review."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='saved_questions')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='saved_by')
    note = models.TextField(blank=True)
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'question']
        ordering = ['-saved_at']
        verbose_name = 'Saved Question'
        verbose_name_plural = 'Saved Questions'

    def __str__(self):
        return f"{self.user.email} saved Q{self.question.number}"


# ─── QUESTION BANK ────────────────────────────────────────────────────────────

class BankQuestion(models.Model):
    """Standalone question bank — imported via JSON (Uzbek format)."""

    class Subject(models.TextChoices):
        MATH = 'Matematika', 'Matematika'
        ENGLISH = 'Ingliz tili', 'Ingliz tili'
        READING = 'Reading', 'Reading & Writing'
        OTHER = 'Boshqa', 'Boshqa'

    class Difficulty(models.TextChoices):
        EASY = 'easy', 'Oson'
        MEDIUM = 'medium', "O'rta"
        HARD = 'hard', 'Qiyin'

    class QuestionType(models.TextChoices):
        MCQ = 'MCQ', 'Multiple Choice'
        INPUT = 'INPUT', 'Student-Produced Response'

    # Math SAT categories
    class MathCategory(models.TextChoices):
        ALGEBRA = 'algebra', 'Algebra'
        ADVANCED_MATH = 'advanced_math', 'Advanced Math'
        PROBLEM_DATA = 'problem_data', 'Problem-Solving & Data Analysis'
        GEOMETRY = 'geometry', 'Geometry & Trigonometry'

    # English SAT categories
    class EnglishCategory(models.TextChoices):
        CRAFT_STRUCTURE = 'craft_structure', 'Craft & Structure'
        EXPRESSION_IDEAS = 'expression_ideas', 'Expression of Ideas'
        INFO_IDEAS = 'info_ideas', 'Information & Ideas'
        STANDARD_ENGLISH = 'standard_english', 'Standard English Conventions'

    subject = models.CharField(max_length=50, choices=Subject.choices, default=Subject.MATH, verbose_name='Fan')
    category = models.CharField(max_length=50, blank=True, verbose_name='Kategoriya',
                                help_text='SAT category (algebra, advanced_math, etc.)')
    question_type = models.CharField(max_length=10, choices=QuestionType.choices, default=QuestionType.MCQ,
                                     verbose_name='Savol turi')
    topic = models.CharField(max_length=200, verbose_name='Mavzu')
    content = models.TextField(verbose_name='Savol')
    math_equation = models.TextField(blank=True, verbose_name='Math Equation', help_text='LaTeX tenglama, contentdan oldin markazda chiqadi')
    passage = models.TextField(blank=True, verbose_name='Passage (matn)', help_text='English savollari uchun o\'qish matni, HTML qo\'llab-quvvatlanadi')
    table_data = models.JSONField(null=True, blank=True, verbose_name='Jadval', help_text='[[header1, header2], [row1col1, row1col2], ...]')
    image = models.ImageField(upload_to='bank_questions/', null=True, blank=True, verbose_name='Savol rasmi')
    difficulty = models.CharField(max_length=10, choices=Difficulty.choices, default=Difficulty.MEDIUM, verbose_name='Qiyinlik')

    choice_a = models.TextField(blank=True, verbose_name='A variant')
    choice_b = models.TextField(blank=True, verbose_name='B variant')
    choice_c = models.TextField(blank=True, verbose_name='C variant')
    choice_d = models.TextField(blank=True, verbose_name='D variant')
    choice_a_image = models.ImageField(upload_to='bank_choices/', null=True, blank=True, verbose_name='A rasm')
    choice_b_image = models.ImageField(upload_to='bank_choices/', null=True, blank=True, verbose_name='B rasm')
    choice_c_image = models.ImageField(upload_to='bank_choices/', null=True, blank=True, verbose_name='C rasm')
    choice_d_image = models.ImageField(upload_to='bank_choices/', null=True, blank=True, verbose_name='D rasm')
    correct_answer = models.CharField(max_length=50, verbose_name="To'g'ri javob")

    explanation = models.TextField(blank=True, verbose_name='Izoh')
    year = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Yil')
    source = models.CharField(max_length=100, blank=True, verbose_name='Manba')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Bank Question'
        verbose_name_plural = 'Bank Questions'
        indexes = [
            models.Index(fields=['subject', 'difficulty']),
            models.Index(fields=['subject', 'category']),
            models.Index(fields=['topic']),
        ]

    def __str__(self):
        return f"{self.subject} — {self.topic[:60]}"


class SavedBankQuestion(models.Model):
    """User progress on a bank question; is_bookmarked = explicit Mark for review."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='saved_bank_questions')
    question = models.ForeignKey(BankQuestion, on_delete=models.CASCADE, related_name='saved_by_users')
    user_answer = models.CharField(max_length=50, blank=True)
    is_correct = models.BooleanField(default=False)
    is_bookmarked = models.BooleanField(default=False)
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'question']
        ordering = ['-saved_at']
        verbose_name = 'Saved Bank Question'
        verbose_name_plural = 'Saved Bank Questions'

    def __str__(self):
        return f"{self.user.email} saved BankQ#{self.question.id}"


class QuestionReport(models.Model):
    """User-submitted report about a practice bank question."""

    class Reason(models.TextChoices):
        EXPLANATION    = 'explanation',       'Issue with explanation'
        WRONG_MARKED   = 'wrong_marked',      'Wrong answer marked as correct'
        EXPLANATION_WRONG = 'explanation_wrong', 'Explanation is incorrect'
        FORMATTING     = 'formatting',        'Formatting/display issue'
        OTHER          = 'other',             'Other issue'

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending'
        REVIEWED = 'reviewed', 'Reviewed'
        RESOLVED = 'resolved', 'Resolved'
        IGNORED  = 'ignored',  'Ignored'

    user      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='question_reports')
    question  = models.ForeignKey('BankQuestion', on_delete=models.CASCADE, related_name='reports', null=True, blank=True)
    reason    = models.CharField(max_length=30, choices=Reason.choices, default=Reason.OTHER)
    details   = models.TextField(blank=True)
    status    = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Question Report'
        verbose_name_plural = 'Question Reports'

    def __str__(self):
        return f"Report#{self.id} — {self.reason} by {self.user}"
