FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ffmpeg is required to split podcast audio that exceeds Whisper's 25 MB limit
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    ffmpeg \
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
