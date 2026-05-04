def user_context(request):
    """Add user-related context to all templates."""
    ctx = {}
    if request.user.is_authenticated:
        ctx['user_is_premium'] = request.user.is_premium
        ctx['user_full_name'] = request.user.full_name
    return ctx
