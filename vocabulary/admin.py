from django.contrib import admin
from .models import Word, UserWord


@admin.register(Word)
class WordAdmin(admin.ModelAdmin):
    list_display = ['word', 'difficulty', 'category']
    list_filter = ['difficulty', 'category']
    search_fields = ['word', 'definition']
    ordering = ['word']


@admin.register(UserWord)
class UserWordAdmin(admin.ModelAdmin):
    list_display = ['user', 'word', 'learned', 'review_count', 'next_review']
    list_filter = ['learned']
    search_fields = ['user__email', 'word__word']
    raw_id_fields = ['user', 'word']
