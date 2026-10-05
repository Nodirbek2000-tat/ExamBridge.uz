from django.conf import settings
from django.db import models


class Game(models.Model):
    """One game on the hub. Admin → Games turns it on/off and sets the order."""

    LIVE = 'live'
    SOON = 'soon'
    HIDDEN = 'hidden'
    STATUS_CHOICES = [
        (LIVE, 'Live'),
        (SOON, 'Tez orada'),
        (HIDDEN, 'Yashirin'),
    ]

    slug = models.SlugField(max_length=40, unique=True)
    title = models.CharField(max_length=60)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=SOON)
    order = models.PositiveSmallIntegerField(default=0)
    # admin overrides of the game's tuning (gamestats/configs.py validates them; defaults live in code)
    config = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f'{self.title} ({self.status})'


class GameDay(models.Model):
    """One row per (user, game, day): how many times they opened it and finished a play."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='game_days')
    slug = models.CharField(max_length=40)
    date = models.DateField()
    opens = models.PositiveIntegerField(default=0)
    plays = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'slug', 'date'], name='uniq_gameday_user_slug_date'),
        ]
        indexes = [
            models.Index(fields=['slug', 'date'], name='gameday_slug_date_idx'),
        ]

    def __str__(self):
        return f'{self.user_id} · {self.slug} · {self.date} · {self.opens}/{self.plays}'
