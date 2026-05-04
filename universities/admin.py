from django.contrib import admin
from .models import University, UniversityRecommendation


@admin.register(University)
class UniversityAdmin(admin.ModelAdmin):
    list_display = ('name', 'country', 'min_total_score', 'min_math_score',
                    'min_english_score', 'acceptance_rate', 'ranking', 'is_active')
    list_filter = ('country', 'is_active')
    search_fields = ('name',)
    ordering = ('ranking', 'name')
    list_editable = ('min_total_score', 'min_math_score', 'min_english_score', 'is_active')
    fieldsets = (
        ('Basic Info', {'fields': ('name', 'country', 'logo', 'description', 'website_url')}),
        ('SAT Requirements', {'fields': ('min_total_score', 'min_math_score', 'min_english_score')}),
        ('Stats', {'fields': ('acceptance_rate', 'ranking', 'is_active')}),
    )


@admin.register(UniversityRecommendation)
class UniversityRecommendationAdmin(admin.ModelAdmin):
    list_display = ('user', 'university', 'total_score_used', 'chance', 'created_at')
    search_fields = ('user__email', 'university__name')
    readonly_fields = ('created_at',)
