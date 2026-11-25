FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ffmpeg is required to split podcast audio that exceeds Whisper's 25 MB limit, and converts
# Speaking game recordings to 16 kHz WAV for Azure pronunciation assessment.
# ca-certificates, libssl-dev, libasound2-dev: what Microsoft lists for the Azure Speech SDK on
# Debian / Ubuntu (OpenSSL 3, ALSA; the -dev packages pull in the right runtime library on
# every Debian release, e.g. libasound2 on 12 and libasound2t64 on 13).
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    ffmpeg \
    ca-certificates \
    libssl-dev \
    libasound2-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
# Sozlamalar gunicorn.conf.py da (worker soni, thread, timeout — .env orqali)
CMD ["gunicorn", "config.wsgi:application", "--config", "/app/gunicorn.conf.py"]
