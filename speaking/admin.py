from django.contrib import admin

from .models import SpeakingAttempt, SpeakingLesson


@admin.register(SpeakingLesson)
class SpeakingLessonAdmin(admin.ModelAdmin):
    list_display = ('title', 'level', 'topic', 'order', 'is_premium', 'is_active', 'created_at')
    list_filter = ('level', 'is_premium', 'is_active')
    list_editable = ('order', 'is_premium', 'is_active')
    search_fields = ('title', 'topic', 'text')


@admin.register(SpeakingAttempt)
class SpeakingAttemptAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'lesson', 'status', 'accuracy', 'ok_count', 'fix_count', 'skip_count', 'created_at')
    list_filter = ('status', 'lesson__level')
    search_fields = ('user__email', 'lesson__title')
    raw_id_fields = ('user', 'lesson')
    readonly_fields = ('words', 'transcript', 'created_at')
