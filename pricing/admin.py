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


from .models import CardPayment, PaymentSettings  # noqa: E402


@admin.register(PaymentSettings)
class PaymentSettingsAdmin(admin.ModelAdmin):
    list_display = ('card_label', 'card_number', 'card_holder', 'price_1month', 'price_3month', 'accepting')


@admin.register(CardPayment)
class CardPaymentAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'plan', 'amount', 'status', 'created_at', 'decided_by')
    list_filter = ('status', 'plan')
    search_fields = ('user__email', 'user__first_name', 'telegram_id')
    readonly_fields = ('created_at', 'decided_at', 'premium_until')
