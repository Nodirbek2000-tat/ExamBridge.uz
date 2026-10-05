from django.contrib import admin

from .models import Duel, QSet, Round


@admin.register(Round)
class RoundAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'level', 'mode', 'opp_kind', 'status', 'score', 'correct', 'ranked', 'started_at')
    list_filter = ('level', 'status', 'mode', 'opp_kind', 'ranked')
    raw_id_fields = ('user', 'qset', 'ghost')
    readonly_fields = ('started_at', 'finished_at')


@admin.register(Duel)
class DuelAdmin(admin.ModelAdmin):
    list_display = ('code', 'level', 'creator', 'opponent', 'created_at', 'expires_at')
    raw_id_fields = ('creator', 'opponent', 'qset', 'creator_round', 'opponent_round')
    search_fields = ('code',)


@admin.register(QSet)
class QSetAdmin(admin.ModelAdmin):
    list_display = ('id', 'level', 'n', 'plays', 'created_at')
    list_filter = ('level',)
    raw_id_fields = ('created_by',)
