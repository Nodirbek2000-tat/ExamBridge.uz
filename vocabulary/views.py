from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
import json

from .models import Word, UserWord


@login_required
def vocabulary_view(request):
    category = request.GET.get('cat', '')
    difficulty = request.GET.get('diff', '')

    words = Word.objects.all()
    if category:
        words = words.filter(category__icontains=category)
    if difficulty:
        words = words.filter(difficulty=difficulty)

    # Get user's learned words
    user_words = {uw.word_id: uw for uw in UserWord.objects.filter(user=request.user)}
    total = words.count()
    learned = sum(1 for uw in user_words.values() if uw.learned)

    # Due for review
    due = UserWord.objects.filter(
        user=request.user, next_review__lte=timezone.now(), learned=False
    ).select_related('word').order_by('next_review')[:20]

    categories = Word.objects.values_list('category', flat=True).distinct().exclude(category='')

    return render(request, 'vocabulary/vocabulary.html', {
        'words': words.order_by('word')[:100],
        'user_words': user_words,
        'total': total,
        'learned': learned,
        'due_count': due.count(),
        'due_words': due,
        'categories': categories,
        'selected_cat': category,
        'selected_diff': difficulty,
    })


@login_required
@require_POST
def review_word_view(request, word_id):
    word = get_object_or_404(Word, id=word_id)
    data = json.loads(request.body)
    correct = data.get('correct', False)

    uw, _ = UserWord.objects.get_or_create(user=request.user, word=word)
    uw.mark_reviewed(correct)

    return JsonResponse({'learned': uw.learned, 'next_review': str(uw.next_review.date())})
