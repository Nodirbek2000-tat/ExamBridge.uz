from django.contrib import admin

from .models import BotChannel, TelegramAccount


@admin.register(TelegramAccount)
class TelegramAccountAdmin(admin.ModelAdmin):
    list_display = ('telegram_id', 'username', 'first_name', 'phone', 'user', 'registered_via_bot', 'created_at')
    search_fields = ('telegram_id', 'username', 'phone', 'user__email')
    list_filter = ('registered_via_bot',)


@admin.register(BotChannel)
class BotChannelAdmin(admin.ModelAdmin):
    list_display = ('title', 'chat_id', 'username', 'created_at')
