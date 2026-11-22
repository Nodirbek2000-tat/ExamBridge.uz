from django.contrib import admin

from .models import BotChannel, DailyPost, TelegramAccount


@admin.register(TelegramAccount)
class TelegramAccountAdmin(admin.ModelAdmin):
    list_display = ('telegram_id', 'username', 'first_name', 'phone', 'user', 'registered_via_bot', 'created_at')
    search_fields = ('telegram_id', 'username', 'phone', 'user__email')
    list_filter = ('registered_via_bot',)


@admin.register(BotChannel)
class BotChannelAdmin(admin.ModelAdmin):
    list_display = ('title', 'chat_id', 'username', 'created_at')


@admin.register(DailyPost)
class DailyPostAdmin(admin.ModelAdmin):
    list_display = ('date', 'key', 'button', 'sent', 'gone', 'failed', 'total', 'finished_at')
    readonly_fields = [f.name for f in DailyPost._meta.fields]

    def has_add_permission(self, request):
        return False
