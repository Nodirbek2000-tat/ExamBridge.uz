"""Eski speaking ovoz yozuvlarini o'chirish (qo'lda ishga tushirish uchun).

Har kuni Celery o'zi bajaradi (config/settings.py dagi CELERY_BEAT_SCHEDULE),
bu buyruq esa tekshirish va birinchi marta qo'lda tozalash uchun.

Misollar:
    # nima o'chishini ko'rish (hech narsa o'chirmaydi)
    python manage.py purge_speaking_audio --dry-run

    # standart muddat (.env dagi SPEAKING_AUDIO_RETENTION_DAYS, odatda 120 kun)
    python manage.py purge_speaking_audio

    # boshqa muddat bilan
    python manage.py purge_speaking_audio --days 180
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Belgilangan kundan eski speaking ovoz fayllarini o'chiradi (matn va AI bahosi qoladi)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--days', type=int, default=None,
            help='Necha kundan eski yozuvlar o\'chsin (standart: sozlamadagi qiymat)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Hech narsa o\'chirmaydi, faqat nima o\'chishini ko\'rsatadi',
        )

    def handle(self, *args, **options):
        from ielts.models import SpeakingResponse

        days = options['days'] or getattr(settings, 'SPEAKING_AUDIO_RETENTION_DAYS', 120)
        cutoff = timezone.now() - timedelta(days=days)

        qs = (SpeakingResponse.objects
              .filter(created_at__lt=cutoff)
              .exclude(audio_file='')
              .exclude(audio_file__isnull=True))

        count = qs.count()
        self.stdout.write(f'Muddat: {days} kun  (undan eski: {cutoff:%Y-%m-%d})')
        self.stdout.write(f'Topildi: {count} ta ovoz fayli')

        if not count:
            self.stdout.write(self.style.SUCCESS('O\'chiriladigan narsa yo\'q.'))
            return

        if options['dry_run']:
            total = 0
            for r in qs[:2000]:
                try:
                    total += r.audio_file.size
                except Exception:
                    pass
            self.stdout.write(self.style.WARNING(
                f'[dry-run] Taxminan {total / 1024 / 1024:.1f} MB bo\'shardi. '
                f'Hech narsa o\'chirilmadi.'
            ))
            return

        from api.tasks import purge_old_speaking_audio
        result = purge_old_speaking_audio(days=days)   # to'g'ridan-to'g'ri, Celery'siz

        self.stdout.write(self.style.SUCCESS(
            f"O'chirildi: {result['deleted']} ta  |  "
            f"Bo'shadi: {result['freed_mb']} MB  |  "
            f"Xato: {result['failed']} ta"
        ))
