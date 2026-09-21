"""Vaqtinchalik sinov: speaking audio tozalash to'g'ri ishlaydimi."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone


class PurgeSpeakingAudioTest(TestCase):
    def setUp(self):
        from ielts.models import IELTSAttempt, SpeakingTask, SpeakingResponse

        User = get_user_model()
        self.user = User.objects.create_user(
            username='stud', email='s@t.uz', password='x'
        )
        self.attempt = IELTSAttempt.objects.create(user=self.user)
        self.task = SpeakingTask.objects.create(title='Part 1', part=1)
        self.SpeakingResponse = SpeakingResponse

    def _make(self, age_days, with_audio=True):
        r = self.SpeakingResponse.objects.create(
            attempt=self.attempt,
            task=self.task,
            transcript='men shunday dedim',
            ai_feedback='yaxshi',
            ai_band=6.5,
            ai_criteria={'fluency': 6},
        )
        if with_audio:
            r.audio_file.save(f'rec-{r.id}.webm', ContentFile(b'x' * 1024), save=True)
        # created_at auto_now_add — to'g'ridan-to'g'ri yangilaymiz
        self.SpeakingResponse.objects.filter(id=r.id).update(
            created_at=timezone.now() - timedelta(days=age_days)
        )
        return self.SpeakingResponse.objects.get(id=r.id)

    def test_faqat_eski_yozuvlar_ochadi(self):
        from api.tasks import purge_old_speaking_audio

        yangi = self._make(10)      # 10 kunlik  — qolishi kerak
        chegara = self._make(119)   # 119 kunlik — qolishi kerak
        eski = self._make(121)      # 121 kunlik — o'chishi kerak
        juda_eski = self._make(400)

        result = purge_old_speaking_audio(days=120)
        self.assertEqual(result['deleted'], 2, result)

        yangi.refresh_from_db()
        chegara.refresh_from_db()
        eski.refresh_from_db()
        juda_eski.refresh_from_db()

        self.assertTrue(yangi.audio_file, '10 kunlik yozuv o\'chib ketdi')
        self.assertTrue(chegara.audio_file, '119 kunlik yozuv o\'chib ketdi')
        self.assertFalse(eski.audio_file, '121 kunlik yozuv o\'chmadi')
        self.assertFalse(juda_eski.audio_file, '400 kunlik yozuv o\'chmadi')

    def test_matn_va_ai_bahosi_saqlanadi(self):
        from api.tasks import purge_old_speaking_audio

        r = self._make(200)
        purge_old_speaking_audio(days=120)
        r.refresh_from_db()

        self.assertFalse(r.audio_file, 'ovoz fayli o\'chmadi')
        # Eng muhimi: o'quvchining natijasi joyida qolishi kerak
        self.assertEqual(r.transcript, 'men shunday dedim')
        self.assertEqual(r.ai_feedback, 'yaxshi')
        self.assertEqual(float(r.ai_band), 6.5)
        self.assertEqual(r.ai_criteria, {'fluency': 6})

    def test_fayl_diskdan_ham_ochadi(self):
        from django.core.files.storage import default_storage
        from api.tasks import purge_old_speaking_audio

        r = self._make(200)
        path = r.audio_file.name
        self.assertTrue(default_storage.exists(path))

        purge_old_speaking_audio(days=120)
        self.assertFalse(default_storage.exists(path), 'fayl diskda qoldi')

    def test_qayta_ishga_tushirsa_xato_bermaydi(self):
        from api.tasks import purge_old_speaking_audio

        self._make(200)
        first = purge_old_speaking_audio(days=120)
        second = purge_old_speaking_audio(days=120)
        self.assertEqual(first['deleted'], 1)
        self.assertEqual(second['deleted'], 0, 'ikkinchi marta ham o\'chirmoqchi bo\'ldi')
        self.assertEqual(second['failed'], 0)

    def test_ovozsiz_yozuvlar_tegilmaydi(self):
        from api.tasks import purge_old_speaking_audio

        r = self._make(300, with_audio=False)
        result = purge_old_speaking_audio(days=120)
        self.assertEqual(result['deleted'], 0)
        self.assertEqual(result['failed'], 0)
        r.refresh_from_db()
        self.assertEqual(r.transcript, 'men shunday dedim')

    def test_sozlamadagi_muddat_120_kun(self):
        from django.conf import settings
        self.assertEqual(settings.SPEAKING_AUDIO_RETENTION_DAYS, 120)

    def test_boshqaruv_buyrugi_ishlaydi(self):
        """`manage.py purge_speaking_audio` — qo'lda ishga tushirish yo'li."""
        from io import StringIO
        from django.core.management import call_command

        eski = self._make(200)
        yangi = self._make(10)

        # dry-run hech narsani o'chirmasligi kerak
        out = StringIO()
        call_command('purge_speaking_audio', '--dry-run', stdout=out)
        eski.refresh_from_db()
        self.assertTrue(eski.audio_file, 'dry-run fayilni o\'chirib yubordi')
        self.assertIn('dry-run', out.getvalue())

        # haqiqiy ishga tushirish
        out = StringIO()
        call_command('purge_speaking_audio', stdout=out)
        eski.refresh_from_db()
        yangi.refresh_from_db()
        self.assertFalse(eski.audio_file)
        self.assertTrue(yangi.audio_file)

    def test_beat_jadvali_royxatdan_otgan(self):
        from django.conf import settings
        sched = settings.CELERY_BEAT_SCHEDULE
        self.assertIn('purge-old-speaking-audio', sched)
        self.assertEqual(sched['purge-old-speaking-audio']['task'],
                         'api.tasks.purge_old_speaking_audio')
