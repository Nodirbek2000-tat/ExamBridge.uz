"""
Courses app models — ready but URLs not yet active.
"""
from django.db import models
from django.conf import settings


class Course(models.Model):
    class SectionType(models.TextChoices):
        ENGLISH = 'ENGLISH', 'Reading & Writing'
        MATH = 'MATH', 'Math'
        GENERAL = 'GENERAL', 'General SAT'

    title = models.CharField(max_length=200)
    description = models.TextField()
    section_type = models.CharField(max_length=10, choices=SectionType.choices, default=SectionType.GENERAL)
    thumbnail = models.ImageField(upload_to='courses/', null=True, blank=True)
    is_premium = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'title']
        verbose_name = 'Course'
        verbose_name_plural = 'Courses'

    def __str__(self):
        return self.title

    @property
    def lesson_count(self):
        return self.lessons.filter(is_active=True).count()


class Lesson(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='lessons')
    title = models.CharField(max_length=200)
    content = models.TextField(help_text='HTML content with LaTeX support')
    video_url = models.URLField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']
        verbose_name = 'Lesson'
        verbose_name_plural = 'Lessons'

    def __str__(self):
        return f"{self.course.title} - {self.title}"


class UserCourse(models.Model):
    """Track user enrollment and progress."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='user_courses')
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='enrollments')
    completed_lessons = models.JSONField(default=list)  # list of lesson IDs
    enrolled_at = models.DateTimeField(auto_now_add=True)
    last_accessed = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['user', 'course']
        verbose_name = 'User Course'
        verbose_name_plural = 'User Courses'

    def __str__(self):
        return f"{self.user.email} - {self.course.title}"

    @property
    def progress_percent(self):
        total = self.course.lesson_count
        if total == 0:
            return 0
        return int(len(self.completed_lessons) / total * 100)

    def mark_lesson_complete(self, lesson_id):
        if lesson_id not in self.completed_lessons:
            self.completed_lessons.append(lesson_id)
            self.save(update_fields=['completed_lessons', 'last_accessed'])
