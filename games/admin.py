from django.contrib import admin

from .models import ShadowingText, ShadowingAttempt


@admin.register(ShadowingText)
class ShadowingTextAdmin(admin.ModelAdmin):
    list_display = ('title', 'topic', 'level', 'word_count', 'is_premium', 'created_at')
    list_filter = ('level', 'is_premium')
    search_fields = ('title', 'topic', 'body')


@admin.register(ShadowingAttempt)
class ShadowingAttemptAdmin(admin.ModelAdmin):
    list_display = ('user', 'text', 'overall_score', 'accuracy_pct', 'fluency_wpm', 'cefr_estimate', 'created_at')
    list_filter = ('cefr_estimate',)
    search_fields = ('user__email', 'text__title')
    readonly_fields = ('word_results', 'transcript')
