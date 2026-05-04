from django.contrib import admin
from django.utils.html import format_html
from .models import (
    IELTSTest,
    ReadingPassage, ReadingQuestion, ReadingChoice,
    ListeningSection, ListeningQuestion, ListeningChoice,
    SpeakingTask,
    WritingTask,
    IELTSAttempt, ReadingAnswer, ListeningAnswer,
    SpeakingResponse, WritingResponse,
)


# ── Inlines ───────────────────────────────────────────────────────────────────

class ReadingChoiceInline(admin.TabularInline):
    model = ReadingChoice
    extra = 0
    fields = ('option', 'text')


class ReadingQuestionInline(admin.TabularInline):
    model = ReadingQuestion
    extra = 0
    fields = ('number', 'question_type', 'content', 'correct_answer')
    show_change_link = True


class ListeningChoiceInline(admin.TabularInline):
    model = ListeningChoice
    extra = 0
    fields = ('option', 'text')


class ListeningQuestionInline(admin.TabularInline):
    model = ListeningQuestion
    extra = 0
    fields = ('number', 'question_type', 'content', 'correct_answer')
    show_change_link = True


# ── IELTS Test ────────────────────────────────────────────────────────────────

@admin.register(IELTSTest)
class IELTSTestAdmin(admin.ModelAdmin):
    list_display = ('title', 'test_type', 'is_premium', 'is_active', 'created_at')
    list_filter = ('test_type', 'is_premium', 'is_active')
    search_fields = ('title',)
    list_editable = ('is_premium', 'is_active')
    ordering = ('-created_at',)


# ── Reading ───────────────────────────────────────────────────────────────────

@admin.register(ReadingPassage)
class ReadingPassageAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'passage_number', 'question_count', 'time_limit', 'is_standalone', 'is_premium', 'created_at')
    list_filter = ('is_standalone', 'is_premium')
    search_fields = ('title',)
    list_editable = ('is_premium', 'is_standalone')
    inlines = [ReadingQuestionInline]
    ordering = ('passage_number',)

    def question_count(self, obj):
        return obj.questions.count()
    question_count.short_description = 'Questions'


@admin.register(ReadingQuestion)
class ReadingQuestionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'question_type', 'correct_answer', 'passage')
    list_filter = ('question_type', 'passage')
    search_fields = ('content',)
    inlines = [ReadingChoiceInline]


# ── Listening ─────────────────────────────────────────────────────────────────

@admin.register(ListeningSection)
class ListeningSectionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'section_number', 'question_count', 'has_audio', 'is_standalone', 'is_premium', 'created_at')
    list_filter = ('is_standalone', 'is_premium')
    search_fields = ('title',)
    list_editable = ('is_premium', 'is_standalone')
    inlines = [ListeningQuestionInline]
    ordering = ('section_number',)

    def question_count(self, obj):
        return obj.questions.count()
    question_count.short_description = 'Questions'

    def has_audio(self, obj):
        return bool(obj.audio_file)
    has_audio.boolean = True
    has_audio.short_description = 'Audio'


@admin.register(ListeningQuestion)
class ListeningQuestionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'question_type', 'correct_answer', 'section')
    list_filter = ('question_type', 'section')
    search_fields = ('content',)
    inlines = [ListeningChoiceInline]


# ── Speaking ──────────────────────────────────────────────────────────────────

@admin.register(SpeakingTask)
class SpeakingTaskAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'part', 'prep_time_display', 'speak_time_display', 'is_premium', 'created_at')
    list_filter = ('part', 'is_premium')
    search_fields = ('title', 'prompt')
    list_editable = ('is_premium',)
    ordering = ('part', 'title')

    def prep_time_display(self, obj):
        return f"{obj.prep_time}s"
    prep_time_display.short_description = 'Prep'

    def speak_time_display(self, obj):
        return f"{obj.speak_time}s"
    speak_time_display.short_description = 'Speak'


# ── Writing ───────────────────────────────────────────────────────────────────

@admin.register(WritingTask)
class WritingTaskAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'task_type', 'test_type', 'min_words', 'time_limit', 'is_premium', 'created_at')
    list_filter = ('task_type', 'test_type', 'is_premium')
    search_fields = ('title', 'prompt')
    list_editable = ('is_premium',)
    ordering = ('-created_at',)

    def has_image(self, obj):
        return bool(obj.image)
    has_image.boolean = True
    has_image.short_description = 'Chart'


# ── Attempts ──────────────────────────────────────────────────────────────────

class ReadingAnswerInline(admin.TabularInline):
    model = ReadingAnswer
    extra = 0
    readonly_fields = ('question', 'answer', 'is_correct', 'time_spent')
    can_delete = False


class ListeningAnswerInline(admin.TabularInline):
    model = ListeningAnswer
    extra = 0
    readonly_fields = ('question', 'answer', 'is_correct')
    can_delete = False


class SpeakingResponseInline(admin.TabularInline):
    model = SpeakingResponse
    extra = 0
    readonly_fields = ('task', 'audio_file', 'ai_band', 'ai_feedback', 'created_at')
    can_delete = False


class WritingResponseInline(admin.TabularInline):
    model = WritingResponse
    extra = 0
    readonly_fields = ('task', 'word_count', 'ai_band', 'ai_criteria', 'created_at')
    can_delete = False


@admin.register(IELTSAttempt)
class IELTSAttemptAdmin(admin.ModelAdmin):
    list_display = ('user', 'test', 'status', 'overall_band', 'band_summary', 'tab_switches', 'started_at')
    list_filter = ('status',)
    search_fields = ('user__email',)
    readonly_fields = ('started_at', 'finished_at', 'security_events')
    inlines = [ReadingAnswerInline, ListeningAnswerInline, SpeakingResponseInline, WritingResponseInline]
    ordering = ('-started_at',)

    def band_summary(self, obj):
        parts = []
        if obj.reading_band:   parts.append(f"R:{obj.reading_band}")
        if obj.listening_band: parts.append(f"L:{obj.listening_band}")
        if obj.speaking_band:  parts.append(f"S:{obj.speaking_band}")
        if obj.writing_band:   parts.append(f"W:{obj.writing_band}")
        return ' | '.join(parts) if parts else '—'
    band_summary.short_description = 'Band Scores'


@admin.register(SpeakingResponse)
class SpeakingResponseAdmin(admin.ModelAdmin):
    list_display = ('attempt', 'task', 'ai_band', 'created_at')
    list_filter = ('ai_band',)
    readonly_fields = ('created_at',)
    search_fields = ('attempt__user__email',)


@admin.register(WritingResponse)
class WritingResponseAdmin(admin.ModelAdmin):
    list_display = ('attempt', 'task', 'word_count', 'ai_band', 'created_at')
    list_filter = ('ai_band', 'task__task_type')
    readonly_fields = ('word_count', 'created_at')
    search_fields = ('attempt__user__email',)
