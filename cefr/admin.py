from django.contrib import admin
from django.utils.html import format_html
from .models import (
    CEFRTest, CEFRQuestion, CEFRChoice,
    CEFRAttempt, CEFRAnswer,
)


# ── Inlines ───────────────────────────────────────────────────────────────────

class CEFRChoiceInline(admin.TabularInline):
    model = CEFRChoice
    extra = 0
    max_num = 6
    fields = ('option', 'text')


class CEFRQuestionInline(admin.TabularInline):
    model = CEFRQuestion
    extra = 0
    fields = ('number', 'question_type', 'content', 'correct_answer')
    show_change_link = True


class CEFRAnswerInline(admin.TabularInline):
    model = CEFRAnswer
    extra = 0
    readonly_fields = ('question', 'answer', 'is_correct')
    can_delete = False


# ── CEFR Test ─────────────────────────────────────────────────────────────────

@admin.register(CEFRTest)
class CEFRTestAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'level_badge', 'test_type', 'question_count', 'time_limit', 'is_premium', 'is_active', 'created_at')
    list_filter = ('level', 'test_type', 'is_premium', 'is_active')
    search_fields = ('title', 'description')
    list_editable = ('is_premium', 'is_active')
    ordering = ('level', '-created_at')
    inlines = [CEFRQuestionInline]

    def level_badge(self, obj):
        colors = {
            'A1': '#16a34a', 'A2': '#0d9488',
            'B1': '#2563eb', 'B2': '#4f46e5',
            'C1': '#d97706', 'C2': '#ea580c',
        }
        color = colors.get(obj.level, '#6b7280')
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700">{}</span>',
            color, obj.level
        )
    level_badge.short_description = 'Level'

    def question_count(self, obj):
        return obj.questions.count()
    question_count.short_description = 'Questions'


# ── CEFR Question ─────────────────────────────────────────────────────────────

@admin.register(CEFRQuestion)
class CEFRQuestionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'question_type', 'correct_answer', 'test_level')
    list_filter = ('question_type', 'test__level', 'test__test_type')
    search_fields = ('content', 'passage')
    inlines = [CEFRChoiceInline]
    ordering = ('test__level', 'number')

    def test_level(self, obj):
        return obj.test.level
    test_level.short_description = 'Level'
    test_level.admin_order_field = 'test__level'


# ── CEFR Attempt ─────────────────────────────────────────────────────────────

@admin.register(CEFRAttempt)
class CEFRAttemptAdmin(admin.ModelAdmin):
    list_display = ('user', 'test', 'status', 'score_display', 'level_achieved', 'tab_switches', 'started_at')
    list_filter = ('status', 'test__level')
    search_fields = ('user__email',)
    readonly_fields = ('started_at', 'finished_at', 'security_events')
    inlines = [CEFRAnswerInline]
    ordering = ('-started_at',)

    def score_display(self, obj):
        if obj.score_percent is not None:
            pct = obj.score_percent
            color = '#16a34a' if pct >= 70 else '#d97706' if pct >= 50 else '#dc2626'
            return format_html(
                '<span style="color:{};font-weight:700">{}%</span>',
                color, round(pct, 1)
            )
        return '—'
    score_display.short_description = 'Score'
