from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from .models import University


@login_required
def university_list_view(request):
    query = request.GET.get('q', '')
    score = request.GET.get('score', '')

    universities = University.objects.filter(is_active=True).order_by('ranking', 'name')

    if query:
        universities = universities.filter(
            Q(name__icontains=query) | Q(country__icontains=query)
        )

    user_score = 0
    if score:
        try:
            user_score = int(score)
            universities = universities.filter(min_total_score__lte=user_score)
        except ValueError:
            pass
    elif hasattr(request.user, 'stats') and request.user.stats.best_total_score:
        user_score = request.user.stats.best_total_score

    # Categorize
    reach, match, safety = [], [], []
    if user_score:
        for uni in universities:
            gap = uni.min_total_score - user_score
            if gap > 100:
                reach.append((uni, 'reach'))
            elif -50 <= gap <= 100:
                match.append((uni, 'match'))
            else:
                safety.append((uni, 'safety'))
        categorized = reach + match + safety
    else:
        categorized = [(u, '') for u in universities]

    return render(request, 'universities/university_list.html', {
        'categorized': categorized,
        'query': query,
        'user_score': user_score,
        'reach_count': len(reach),
        'match_count': len(match),
        'safety_count': len(safety),
    })
