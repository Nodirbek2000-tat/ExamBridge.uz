from django.contrib import admin

from .models import ShadowingText, ShadowingAttempt, VoiceGameProgress, VoiceGameRun


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


class _ReadOnlyAdmin(admin.ModelAdmin):
    """Rows are written by the games themselves — admins only look."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(VoiceGameProgress)
class VoiceGameProgressAdmin(_ReadOnlyAdmin):
    list_display = ('user', 'slug', 'best_score', 'plays', 'stars_total', 'updated_at')
    list_filter = ('slug',)
    search_fields = ('user__email',)
    list_select_related = ('user',)


@admin.register(VoiceGameRun)
class VoiceGameRunAdmin(_ReadOnlyAdmin):
    list_display = ('user', 'slug', 'score', 'stars', 'accuracy', 'level', 'duration_sec', 'lines_said', 'created_at')
    list_filter = ('slug',)
    search_fields = ('user__email',)
    list_select_related = ('user',)
    date_hierarchy = 'created_at'
