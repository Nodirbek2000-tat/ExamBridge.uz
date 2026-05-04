"""
SAT Scoring System
Real SAT score conversion: raw score → scaled score (200-800)
"""
from .models import TestResult, TestAttempt


# SAT Math raw → scaled score conversion table (approximate)
MATH_CONVERSION = {
    0: 200, 1: 210, 2: 220, 3: 230, 4: 240, 5: 250,
    6: 260, 7: 270, 8: 280, 9: 290, 10: 300,
    11: 310, 12: 320, 13: 330, 14: 340, 15: 350,
    16: 360, 17: 380, 18: 400, 19: 420, 20: 440,
    21: 460, 22: 480, 23: 500, 24: 520, 25: 540,
    26: 560, 27: 580, 28: 600, 29: 620, 30: 640,
    31: 660, 32: 680, 33: 700, 34: 720, 35: 740,
    36: 760, 37: 780, 38: 790, 39: 790, 40: 800,
    41: 800, 42: 800, 43: 800, 44: 800,
}

# SAT English (Reading & Writing) raw → scaled score conversion
ENGLISH_CONVERSION = {
    0: 200, 1: 210, 2: 220, 3: 230, 4: 240, 5: 250,
    6: 260, 7: 270, 8: 280, 9: 290, 10: 300,
    11: 310, 12: 320, 13: 340, 14: 360, 15: 380,
    16: 400, 17: 420, 18: 440, 19: 460, 20: 470,
    21: 480, 22: 490, 23: 500, 24: 510, 25: 520,
    26: 530, 27: 540, 28: 550, 29: 560, 30: 570,
    31: 580, 32: 590, 33: 600, 34: 610, 35: 620,
    36: 630, 37: 640, 38: 650, 39: 660, 40: 680,
    41: 700, 42: 720, 43: 740, 44: 760, 45: 780,
    46: 790, 47: 790, 48: 800, 49: 800, 50: 800,
    51: 800, 52: 800, 53: 800, 54: 800,
}


def raw_to_scaled_math(raw: int) -> int:
    raw = max(0, min(raw, 44))
    return MATH_CONVERSION.get(raw, 200)


def raw_to_scaled_english(raw: int) -> int:
    raw = max(0, min(raw, 54))
    return ENGLISH_CONVERSION.get(raw, 200)


def calculate_sat_score(attempt: TestAttempt) -> TestResult:
    """Calculate SAT scores for a completed attempt."""

    # Get all answers
    answers = attempt.answers.select_related(
        'question__module__section'
    ).all()

    # Separate by section and module
    math_m1_correct = 0
    math_m2_correct = 0
    english_m1_correct = 0
    english_m2_correct = 0

    for answer in answers:
        section_type = answer.question.module.section.section_type
        module_number = answer.question.module.module_number
        if answer.is_correct:
            if section_type == 'MATH':
                if module_number == 1:
                    math_m1_correct += 1
                else:
                    math_m2_correct += 1
            else:  # ENGLISH
                if module_number == 1:
                    english_m1_correct += 1
                else:
                    english_m2_correct += 1

    math_raw = math_m1_correct + math_m2_correct
    english_raw = english_m1_correct + english_m2_correct

    math_score = raw_to_scaled_math(math_raw)
    english_score = raw_to_scaled_english(english_raw)
    total_score = math_score + english_score

    result, _ = TestResult.objects.update_or_create(
        attempt=attempt,
        defaults={
            'user': attempt.user,
            'math_score': math_score,
            'english_score': english_score,
            'total_score': total_score,
            'math_raw': math_raw,
            'english_raw': english_raw,
            'math_m1_correct': math_m1_correct,
            'math_m2_correct': math_m2_correct,
            'english_m1_correct': english_m1_correct,
            'english_m2_correct': english_m2_correct,
        }
    )

    # Update user stats
    _update_user_stats(attempt.user, result)

    return result


def _update_user_stats(user, result):
    from accounts.models import UserStats
    stats, _ = UserStats.objects.get_or_create(user=user)

    stats.total_tests_taken += 1
    if result.total_score > stats.best_total_score:
        stats.best_total_score = result.total_score
    if result.math_score > stats.best_math_score:
        stats.best_math_score = result.math_score
    if result.english_score > stats.best_english_score:
        stats.best_english_score = result.english_score

    # Recalculate average
    all_results = user.results.all()
    if all_results.exists():
        stats.avg_total_score = sum(r.total_score for r in all_results) / all_results.count()

    stats.update_streak()
    stats.save()
