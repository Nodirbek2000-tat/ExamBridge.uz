from django.contrib import admin
from .models import Plan, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'plan_type', 'price', 'duration_days', 'is_active', 'order')
    list_editable = ('price', 'duration_days', 'is_active', 'order')
    ordering = ('order',)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'plan', 'status', 'started_at', 'expires_at')
    list_filter = ('status', 'plan')
    search_fields = ('user__email',)
    readonly_fields = ('started_at',)
