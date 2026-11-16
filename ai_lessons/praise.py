"""
Toby's short praise for a right answer ("Great job!"), in each lesson voice.

The same few lines serve every lesson, so each is recorded once per language
and voice (by the voice job of the first lesson that needs it) and kept at a
fixed path in the media storage.
"""
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

LINES = {
    'en': ['Great job!', 'Exactly right!', 'You nailed it!', 'Brilliant, well done!', "Yes! That's it!", 'Perfect!'],
    'uz': ["Barakalla!", "To'ppa-to'g'ri!", "Ofarin, zo'r!", "Juda yaxshi!", "Mana bu boshqa gap!", "A'lo!"],
}


def _path(language, voice, index):
    return f'ai_lessons/praise/{language}/{voice}/{index}.mp3'


def lines(language):
    return LINES.get(language, LINES['en'])


def missing(language, voice):
    """The (index, text) of the lines not recorded yet for this voice."""
    return [(i, text) for i, text in enumerate(lines(language))
            if not default_storage.exists(_path(language, voice, i))]


def save(language, voice, index, audio):
    path = _path(language, voice, index)
    if default_storage.exists(path):
        default_storage.delete(path)
    default_storage.save(path, ContentFile(audio))


def urls(language, voice, request):
    """The recorded lines, as links the player can play."""
    found = []
    for i in range(len(lines(language))):
        path = _path(language, voice, i)
        if default_storage.exists(path):
            found.append(request.build_absolute_uri(default_storage.url(path)))
    return found
