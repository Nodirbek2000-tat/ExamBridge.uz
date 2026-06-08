from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, UserStats


class UserStatsInline(admin.StackedInline):
    model = UserStats
    can_delete = False
    verbose_name_plural = 'Statistics'
    fields = ('total_tests_taken', 'best_total_score', 'best_math_score',
              'best_english_score', 'avg_total_score', 'current_streak')
    readonly_fields = ('total_tests_taken', 'best_total_score', 'best_math_score',
                       'best_english_score', 'avg_total_score', 'current_streak')


class ExamBridgeUserFilter(admin.SimpleListFilter):
    title = 'Platform'
    parameter_name = 'platform'

    def lookups(self, request, model_admin):
        return [
            ('exambridge', 'ExamBridge Users'),
            ('testmakon', 'TestMakon Users'),
        ]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(platform=self.value())
        return queryset


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = [UserStatsInline]
    list_display = ('email', 'full_name', 'platform', 'is_premium', 'profile_completed',
                    'date_joined', 'is_active', 'is_staff')
    list_filter = ('platform', 'is_premium', 'is_active', 'is_staff', 'profile_completed', 'country', ExamBridgeUserFilter)
    search_fields = ('email', 'first_name', 'last_name', 'phone', 'platform_uid')
    ordering = ('-date_joined',)
    readonly_fields = ('last_ip', 'created_at', 'updated_at', 'failed_login_attempts', 'platform_uid')

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('first_name', 'last_name', 'avatar', 'bio', 'phone', 'country')}),
        ('Platform', {'fields': ('platform', 'platform_uid')}),
        ('Premium', {'fields': ('is_premium', 'premium_until')}),
        ('Security', {'fields': ('last_ip', 'failed_login_attempts', 'locked_until')}),
        ('Status', {'fields': ('profile_completed', 'is_active', 'is_staff', 'is_superuser')}),
        ('Timestamps', {'fields': ('date_joined', 'created_at', 'updated_at')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2', 'first_name', 'last_name', 'platform'),
        }),
    )


# Proxy model for TestMakon users — shown as separate section in admin
class TestMakonUser(User):
    class Meta:
        proxy = True
        verbose_name = 'TestMakon User'
        verbose_name_plural = 'TestMakon Users'


@admin.register(TestMakonUser)
class TestMakonUserAdmin(BaseUserAdmin):
    list_display = ('email', 'full_name', 'platform_uid', 'is_premium', 'premium_until', 'date_joined', 'is_active')
    list_filter = ('is_premium', 'is_active')
    search_fields = ('email', 'first_name', 'last_name', 'platform_uid')
    ordering = ('-date_joined',)
    readonly_fields = ('platform', 'platform_uid', 'last_ip', 'created_at', 'updated_at', 'failed_login_attempts')

    def get_queryset(self, request):
        return super().get_queryset(request).filter(platform='testmakon')

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('first_name', 'last_name', 'phone')}),
        ('Platform', {'fields': ('platform', 'platform_uid')}),
        ('Premium', {'fields': ('is_premium', 'premium_until')}),
        ('Status', {'fields': ('is_active',)}),
        ('Timestamps', {'fields': ('date_joined', 'created_at', 'updated_at')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2', 'first_name', 'last_name'),
        }),
    )


@admin.register(UserStats)
class UserStatsAdmin(admin.ModelAdmin):
    list_display = ('user', 'total_tests_taken', 'best_total_score', 'current_streak')
    search_fields = ('user__email',)
    readonly_fields = ('updated_at',)
