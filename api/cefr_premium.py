"""
Premium CEFR tests: which ones an account may start, and the admin switch for each.

A Premium test (a reading/listening mock, a single part, a writing or a speaking test)
starts only for accounts with Premium; staff may open everything to check it.
"""
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from cefr.models import CEFRListeningSection, CEFRReadingPassage, CEFRSpeakingTest, CEFRTest, CEFRWritingTest

REQUIRED = {'detail': 'Premium required.', 'code': 'premium_required'}

# what the admin switch can be set on: /api/admin/cefr/premium/<kind>/<pk>/
KINDS = {
    'test': CEFRTest,                    # a reading / listening mock (all its parts)
    'reading': CEFRReadingPassage,       # a single reading part
    'listening': CEFRListeningSection,   # a single listening part
    'writing': CEFRWritingTest,
    'speaking': CEFRSpeakingTest,
}


def locked(user, *items):
    """True when one of `items` is Premium and this account may not open it."""
    if user.is_staff or getattr(user, 'is_premium', False):
        return False
    return any(getattr(item, 'is_premium', False) for item in items if item is not None)


def refused():
    return Response(REQUIRED, status=403)


@api_view(['PATCH'])
@permission_classes([IsAdminUser])
def admin_cefr_premium(request, kind, pk):
    """{'is_premium': true | false} sets it; an empty body switches it."""
    model = KINDS.get(kind)
    if model is None:
        return Response({'detail': f'kind must be one of: {", ".join(KINDS)}'}, status=400)
    item = get_object_or_404(model, pk=pk)
    wanted = request.data.get('is_premium')
    item.is_premium = (not item.is_premium) if wanted is None else bool(wanted)
    item.save(update_fields=['is_premium'])
    return Response({'kind': kind, 'id': item.pk, 'is_premium': item.is_premium})
