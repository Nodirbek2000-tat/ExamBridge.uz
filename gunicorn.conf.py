"""Gunicorn konfiguratsiyasi — exambridge.uz backend.

Nega gthread?
    Oldin `--workers 4` (sync worker) ishlatilardi: bir vaqtda atigi 4 ta so'rov.
    Listening audio yuklanishi yoki AI so'rovi (OpenAI streaming, TTS) bitta
    workerni 10-60 soniya to'liq qulflab turadi — 4 kishi AI chat ochsa butun
    sayt to'xtardi.

    gthread bilan har bir worker ichida `threads` ta oqim bo'ladi. Tarmoqni
    kutayotgan so'rov (OpenAI, fayl uzatish) oqimni band qiladi, lekin protsess
    boshqa so'rovlarga xizmat qilaverradi.

Parallel so'rovlar soni = workers × threads.

Muhim: har bir oqim o'ziga PostgreSQL ulanishi oladi (CONN_MAX_AGE=60).
Shuning uchun workers × threads qiymati Postgres `max_connections` dan kam
bo'lishi shart — docker-compose.yml da max_connections=300 qilib qo'yildi,
bu yerdagi standart qiymatlar esa eng ko'pi 12×6 = 72 ulanish beradi.

Hammasini .env orqali o'zgartirsa bo'ladi (GUNICORN_WORKERS, GUNICORN_THREADS...).
"""

import multiprocessing
import os


def _env_int(name, default):
    """.env dagi bo'sh yoki noto'g'ri qiymat serverni yiqitmasin."""
    try:
        value = int(os.environ.get(name, '') or 0)
    except ValueError:
        value = 0
    return value if value > 0 else default


_cpu_count = multiprocessing.cpu_count()

bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:8000')

# gthread — I/O kutadigan so'rovlar (audio, OpenAI) uchun. 'sync' ga qaytarish
# kerak bo'lsa: GUNICORN_WORKER_CLASS=sync
worker_class = os.environ.get('GUNICORN_WORKER_CLASS', 'gthread')

# 2×CPU+1 klassik formula, lekin DB ulanishlari uchun 12 ta bilan cheklangan
workers = _env_int('GUNICORN_WORKERS', min(_cpu_count * 2 + 1, 12))
threads = _env_int('GUNICORN_THREADS', 6)

# AI (writing/speaking tahlil, TTS) so'rovlari uzoq davom etadi
timeout = _env_int('GUNICORN_TIMEOUT', 120)
# timeout bilan teng: worker qayta ishga tushayotganda yoki deploy paytida
# boshlangan AI so'rovi yarim yo'lda uzilib qolmasin
graceful_timeout = timeout

# nginx bilan keep-alive ulanishlarni ushlab turish — har so'rovda TCP handshake bo'lmaydi
keepalive = 5

# Xotira sizib ketishiga qarshi: worker N ta so'rovdan keyin yumshoq qayta ishga tushadi
max_requests = _env_int('GUNICORN_MAX_REQUESTS', 1000)
max_requests_jitter = 100

loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')
accesslog = '-'
errorlog = '-'


def on_starting(server):
    server.log.info(
        'Gunicorn: %s | workers=%d threads=%d | parallel so\'rov=%d | CPU=%d',
        worker_class, workers, threads, workers * threads, _cpu_count,
    )
