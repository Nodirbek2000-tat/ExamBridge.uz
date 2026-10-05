"""
Word Battle — 15 questions × 7 s on the shared word bank (vocabulary.Word).

    QSet     one frozen set of questions (prompt, options and the key). A ghost and a duel
             replay exactly the same set, so the comparison is fair.
    Round    one learner playing one QSet against an opponent: a recorded real player
             ("ghost"), a bot, or the other side of a duel.
    Answer   one served question: the server's own served_at / answered_at, the verdict, the points.
    Duel     an async duel by link (code), valid for 48 hours; both sides play the same QSet.

The key of a question never leaves the server before that question is answered.
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

LEVELS = ('A2', 'B1', 'B2', 'C1', 'SAT')
LEVEL_CHOICES = [(lv, lv) for lv in LEVELS]
QTYPES = ('en_uz', 'uz_en', 'syn', 'ant', 'cloze', 'listen')
QTYPE_CHOICES = [(t, t) for t in QTYPES]


class QSet(models.Model):
    """questions: [{t, w, p, o: [4 options], k: key index, pos}] — `k` is never sent before the answer."""

    level = models.CharField(max_length=3, choices=LEVEL_CHOICES)
    questions = models.JSONField(default=list)
    n = models.PositiveSmallIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')
    plays = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['level', 'created_at'], name='wb_qset_level_created')]

    def __str__(self):
        return f'QSet {self.pk} · {self.level} · {self.n}'


class Round(models.Model):
    ACTIVE = 'active'
    FINISHED = 'finished'
    ABANDONED = 'abandoned'
    STATUS_CHOICES = [(ACTIVE, 'active'), (FINISHED, 'finished'), (ABANDONED, 'abandoned')]
    MODE_CHOICES = [('solo', 'solo'), ('duel', 'duel')]
    # ghost = a recorded real player · bot = labelled bot · duel = the other side of a duel (recorded)
    # wait = a duel whose other side has not played yet (no live opponent)
    OPP_CHOICES = [('ghost', 'ghost'), ('bot', 'bot'), ('duel', 'duel'), ('wait', 'wait')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wb_rounds')
    qset = models.ForeignKey(QSet, on_delete=models.PROTECT, related_name='rounds')
    level = models.CharField(max_length=3, choices=LEVEL_CHOICES)
    mode = models.CharField(max_length=5, choices=MODE_CHOICES, default='solo')
    opp_kind = models.CharField(max_length=5, choices=OPP_CHOICES, default='bot')
    ghost = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    # {name, label, note, persona?, answers: [[ms, ok, pts], …], score, correct}
    opp = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=ACTIVE)
    cursor = models.PositiveSmallIntegerField(default=0)          # questions served so far
    score = models.PositiveIntegerField(default=0)
    correct = models.PositiveSmallIntegerField(default=0)
    streak = models.PositiveSmallIntegerField(default=0)
    best_streak = models.PositiveSmallIntegerField(default=0)
    complete = models.BooleanField(default=False)                 # every question answered (or timed out)
    ranked = models.BooleanField(default=False)
    flags = models.JSONField(default=list, blank=True)
    question_ms = models.PositiveIntegerField(default=7000)       # config snapshot taken at the start
    grace_ms = models.PositiveIntegerField(default=1500)
    srs = models.JSONField(default=dict, blank=True)              # {strengthened, new, weak}
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # one active round per learner
            models.UniqueConstraint(fields=['user'], condition=Q(status='active'), name='wb_one_active_round'),
        ]
        indexes = [
            models.Index(fields=['user', 'status'], name='wb_round_user_status'),
            models.Index(fields=['level', 'status', 'finished_at'], name='wb_round_level_fin'),
            models.Index(fields=['finished_at'], name='wb_round_finished'),
        ]

    def __str__(self):
        return f'Round {self.pk} · {self.user_id} · {self.level} · {self.status} · {self.score}'


class Answer(models.Model):
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name='answers')
    idx = models.PositiveSmallIntegerField()
    word_id = models.PositiveIntegerField()
    qtype = models.CharField(max_length=6, choices=QTYPE_CHOICES)
    choice = models.SmallIntegerField(null=True, blank=True)      # 0–3, None = no answer (timeout)
    answered = models.BooleanField(default=False)
    correct = models.BooleanField(default=False)
    timeout = models.BooleanField(default=False)
    served_at = models.DateTimeField()
    answered_at = models.DateTimeField(null=True, blank=True)
    ms = models.PositiveIntegerField(null=True, blank=True)       # server time from serve to answer
    points = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['round', 'idx'], name='wb_answer_round_idx')]
        indexes = [models.Index(fields=['answered_at'], name='wb_answer_answered')]

    def __str__(self):
        return f'{self.round_id} #{self.idx} · {self.qtype} · {"ok" if self.correct else "miss"}'


class Duel(models.Model):
    code = models.CharField(max_length=10, unique=True)
    level = models.CharField(max_length=3, choices=LEVEL_CHOICES)
    qset = models.ForeignKey(QSet, on_delete=models.PROTECT, related_name='duels')
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wb_duels_created')
    opponent = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
                                 related_name='wb_duels_joined')
    creator_round = models.ForeignKey(Round, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    opponent_round = models.ForeignKey(Round, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=['creator', 'created_at'], name='wb_duel_creator'),
            models.Index(fields=['opponent', 'created_at'], name='wb_duel_opponent'),
            models.Index(fields=['created_at'], name='wb_duel_created'),
        ]

    def __str__(self):
        return f'Duel {self.code} · {self.level}'
