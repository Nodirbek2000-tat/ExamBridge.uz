from django.contrib import admin
from django.http import JsonResponse
from django.urls import path
from django.utils.html import format_html
import json

from .models import (
    Test, TestSection, Module, Question, Choice,
    TestAttempt, Answer, TestResult, AIAnalysis, SavedQuestion
)


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 4
    max_num = 4
    fields = ('option', 'text', 'image')


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0
    fields = ('number', 'question_type', 'difficulty', 'content')
    show_change_link = True


class ModuleInline(admin.TabularInline):
    model = Module
    extra = 0
    fields = ('module_number', 'time_limit')
    show_change_link = True


class TestSectionInline(admin.TabularInline):
    model = TestSection
    extra = 0
    inlines = [ModuleInline]
    show_change_link = True


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'test_type', 'year', 'month', 'form', 'is_premium', 'is_active', 'section_count')
    list_filter = ('test_type', 'year', 'month', 'is_premium', 'is_active', 'is_international')
    search_fields = ('year', 'form')
    ordering = ('-year', '-month')
    inlines = [TestSectionInline]
    actions = ['import_from_json']

    def section_count(self, obj):
        return obj.sections.count()
    section_count.short_description = 'Sections'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('import-json/', self.admin_site.admin_view(self.import_json_view), name='test_import_json'),
        ]
        return custom_urls + urls

    def import_json_view(self, request):
        """
        JSON import endpoint.

        How to upload questions via JSON:
        POST /admin/tests_app/test/import-json/
        Content-Type: application/json

        Format:
        {
          "test_type": "SAT",
          "year": 2025,
          "month": 3,
          "form": "A",
          "is_international": false,
          "sections": [
            {
              "section_type": "MATH",
              "modules": [
                {
                  "module_number": 1,
                  "time_limit": 35,
                  "questions": [
                    {
                      "number": 1,
                      "question_type": "MCQ",
                      "content": "What is 2+2? \\(2+2=?\\)",
                      "difficulty": "EASY",
                      "correct_answer": "A",
                      "explanation": "Basic addition",
                      "choices": [
                        {"option": "A", "text": "4"},
                        {"option": "B", "text": "3"},
                        {"option": "C", "text": "5"},
                        {"option": "D", "text": "6"}
                      ]
                    }
                  ]
                }
              ]
            }
          ]
        }
        """
        if request.method != 'POST':
            return JsonResponse({'error': 'POST required'}, status=405)

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        try:
            test, created = Test.objects.get_or_create(
                test_type=data['test_type'],
                year=data['year'],
                month=data['month'],
                form=data.get('form', 'A'),
                is_international=data.get('is_international', False),
            )

            stats = {'test_id': test.id, 'created': created, 'questions': 0}

            for sec_data in data.get('sections', []):
                section, _ = TestSection.objects.get_or_create(
                    test=test,
                    section_type=sec_data['section_type'],
                )
                for mod_data in sec_data.get('modules', []):
                    module, _ = Module.objects.get_or_create(
                        section=section,
                        module_number=mod_data['module_number'],
                        defaults={'time_limit': mod_data.get('time_limit', 35)}
                    )
                    for q_data in mod_data.get('questions', []):
                        question, q_created = Question.objects.update_or_create(
                            module=module,
                            number=q_data['number'],
                            defaults={
                                'question_type': q_data.get('question_type', 'MCQ'),
                                'content': q_data.get('content', ''),
                                'passage': q_data.get('passage', ''),
                                'table_data': q_data.get('table_data'),
                                'correct_answer': q_data['correct_answer'],
                                'explanation': q_data.get('explanation', ''),
                                'difficulty': q_data.get('difficulty', 'MEDIUM'),
                            }
                        )
                        if q_created or q_data.get('update_choices'):
                            question.choices.all().delete()
                            for c_data in q_data.get('choices', []):
                                Choice.objects.create(
                                    question=question,
                                    option=c_data['option'],
                                    text=c_data['text'],
                                )
                        stats['questions'] += 1

            return JsonResponse({'success': True, **stats})

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)


@admin.register(TestSection)
class TestSectionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'test', 'section_type')
    list_filter = ('section_type',)
    inlines = [ModuleInline]


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'module_number', 'time_limit', 'question_count')
    list_filter = ('module_number',)
    inlines = [QuestionInline]

    def question_count(self, obj):
        return obj.questions.count()
    question_count.short_description = 'Questions'


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'question_type', 'difficulty', 'has_image')
    list_filter = ('question_type', 'difficulty', 'module__section__section_type')
    search_fields = ('content',)
    inlines = [ChoiceInline]

    def has_image(self, obj):
        return bool(obj.image)
    has_image.boolean = True
    has_image.short_description = 'Image'


@admin.register(TestAttempt)
class TestAttemptAdmin(admin.ModelAdmin):
    list_display = ('user', 'test', 'status', 'started_at', 'finished_at')
    list_filter = ('status',)
    search_fields = ('user__email',)
    readonly_fields = ('started_at', 'finished_at')


@admin.register(TestResult)
class TestResultAdmin(admin.ModelAdmin):
    list_display = ('user', 'total_score', 'math_score', 'english_score', 'calculated_at')
    list_filter = ('total_score',)
    search_fields = ('user__email',)
    readonly_fields = ('calculated_at',)
    ordering = ('-calculated_at',)
