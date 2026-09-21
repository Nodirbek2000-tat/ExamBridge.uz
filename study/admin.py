from django.contrib import admin

from .models import (
    Article, ArticleView, WritingSample, WritingSampleRead, Podcast, PodcastListen,
)


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ('title', 'topic', 'level', 'views_count', 'is_premium', 'order', 'created_at')
    list_filter = ('level', 'is_premium', 'topic')
    search_fields = ('title', 'excerpt', 'topic')
    list_editable = ('order', 'is_premium')


@admin.register(ArticleView)
class ArticleViewAdmin(admin.ModelAdmin):
    list_display = ('article', 'user', 'created_at')
    list_filter = ('created_at',)


@admin.register(WritingSample)
class WritingSampleAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'task_type', 'band', 'is_premium', 'order', 'created_at')
    list_filter = ('task_type', 'band', 'is_premium')
    search_fields = ('prompt', 'essay')
    list_editable = ('order', 'is_premium')


@admin.register(WritingSampleRead)
class WritingSampleReadAdmin(admin.ModelAdmin):
    list_display = ('sample', 'user', 'updated_at')
    list_filter = ('updated_at',)


@admin.register(Podcast)
class PodcastAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'duration_label', 'is_premium', 'order', 'created_at')
    list_filter = ('is_premium',)
    search_fields = ('title', 'author', 'transcript')
    list_editable = ('order', 'is_premium')


@admin.register(PodcastListen)
class PodcastListenAdmin(admin.ModelAdmin):
    list_display = ('podcast', 'user', 'position_sec', 'updated_at')
    list_filter = ('updated_at',)
