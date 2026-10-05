from django.contrib import admin
from .models import Phrase, Review, Word, UserWord


@admin.register(Word)
class WordAdmin(admin.ModelAdmin):
    list_display = ['word', 'uz', 'level', 'topic', 'pos', 'status', 'say_seen', 'say_ok', 'mean_seen', 'mean_ok']
    list_filter = ['status', 'level', 'topic', 'pos', 'speak_risk']
    search_fields = ['word', 'uz', 'definition']
    ordering = ['word']


@admin.register(Phrase)
class PhraseAdmin(admin.ModelAdmin):
    list_display = ['kind', 'level', 'topic', 'text', 'prompt', 'status', 'voice', 'say_seen', 'say_ok']
    list_filter = ['kind', 'status', 'level', 'topic']
    search_fields = ['text', 'prompt', 'uz']
    readonly_fields = ['key']


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ['user', 'kind', 'item_id', 'skill', 'box', 'due_at', 'seen', 'ok', 'miss', 'last_verdict']
    list_filter = ['kind', 'skill', 'box']
    search_fields = ['user__email']
    raw_id_fields = ['user']

    def has_add_permission(self, request):
        return False


@admin.register(UserWord)
class UserWordAdmin(admin.ModelAdmin):
    list_display = ['user', 'word', 'learned', 'review_count', 'next_review']
    list_filter = ['learned']
    search_fields = ['user__email', 'word__word']
    raw_id_fields = ['user', 'word']
