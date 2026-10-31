from django.db import models
from django.conf import settings


class AIStructure(models.Model):
    SUBJECT_CHOICES = [
        ('SAT', 'SAT'), ('IELTS', 'IELTS'), ('CEFR', 'CEFR'), ('GENERAL', 'General'),
    ]
    SECTION_CHOICES = [
        ('math', 'Math'), ('reading_writing', 'Reading & Writing'),
        ('reading', 'Reading'), ('listening', 'Listening'),
        ('writing', 'Writing'), ('speaking', 'Speaking'),
        ('grammar', 'Grammar'), ('general', 'General'),
    ]

    subject = models.CharField(max_length=10, choices=SUBJECT_CHOICES)
    section = models.CharField(max_length=20, choices=SECTION_CHOICES, default='general')
    title = models.CharField(max_length=200)
    content = models.TextField(help_text='Structure/template content in English')
    image = models.ImageField(upload_to='ai_structures/', null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['subject', 'section', 'title']
        verbose_name = 'AI Structure'
        verbose_name_plural = 'AI Structures'

    def __str__(self):
        return f"[{self.subject}/{self.section}] {self.title}"


class AIConversation(models.Model):
    SUBJECT_CHOICES = [('SAT', 'SAT'), ('IELTS', 'IELTS'), ('CEFR', 'CEFR')]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ai_conversations')
    subject = models.CharField(max_length=10, choices=SUBJECT_CHOICES)
    section = models.CharField(max_length=20, default='general')
    title = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.user.email} — {self.subject} — {self.title[:50]}"


class AIMessage(models.Model):
    ROLE_CHOICES = [('user', 'User'), ('assistant', 'Assistant')]

    conversation = models.ForeignKey(AIConversation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    image = models.ImageField(upload_to='ai_messages/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.role}: {self.content[:60]}"


class AIUsage(models.Model):
    """One billed call to an AI provider; cost_usd comes from ai_chat/usage.py at call time."""

    feature = models.CharField(max_length=40)
    model = models.CharField(max_length=40)
    input_tokens = models.PositiveIntegerField(default=0)
    cached_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    audio_seconds = models.FloatField(default=0)
    characters = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['feature', 'created_at'], name='aiusage_feature_created_idx')]

    def __str__(self):
        return f'{self.feature} · {self.model} · ${self.cost_usd}'
