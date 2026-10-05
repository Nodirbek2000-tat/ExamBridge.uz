from django.contrib import admin

from .models import Game, GameDay
from .services import bust_hub_cache


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ('title', 'slug', 'status', 'order', 'updated_at')
    list_editable = ('status', 'order')
    ordering = ('order', 'id')

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        bust_hub_cache()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        bust_hub_cache()

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        bust_hub_cache()


@admin.register(GameDay)
class GameDayAdmin(admin.ModelAdmin):
    list_display = ('date', 'slug', 'user', 'opens', 'plays')
    list_filter = ('slug', 'date')
    search_fields = ('user__email',)
    raw_id_fields = ('user',)
    date_hierarchy = 'date'
